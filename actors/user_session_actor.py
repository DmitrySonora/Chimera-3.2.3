from typing import Dict, Optional, Tuple, List, Any
from pydantic import BaseModel, Field, field_validator, ConfigDict
from datetime import datetime
import asyncio
import logging
import uuid
from actors.base_actor import BaseActor
from actors.messages import ActorMessage, MESSAGE_TYPES
from actors.events import BaseEvent
from config.prompts import PROMPT_CONFIG
from config.settings import STM_CONTEXT_SIZE_FOR_GENERATION, STM_CONTEXT_REQUEST_TIMEOUT
from utils.monitoring import measure_latency
from utils.event_utils import EventVersionManager

class UserSession(BaseModel):
    """Данные сессии пользователя"""
    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        validate_assignment=True
    )
    
    user_id: str
    username: Optional[str] = None
    message_count: int = 0
    created_at: datetime = Field(default_factory=datetime.now)
    last_activity: datetime = Field(default_factory=datetime.now)
    cache_metrics: List[float] = Field(default_factory=list)
    
    # Поля для режимов общения
    current_mode: str = 'talk'
    mode_confidence: float = 0.0
    mode_history: List[str] = Field(default_factory=list)
    last_mode_change: Optional[datetime] = None
    
    # Расширяемость для будущего
    emotional_state: Optional[Any] = None
    style_vector: Optional[Any] = None
    memory_buffer: List[Any] = Field(default_factory=list)
    
    @field_validator('mode_confidence')
    @classmethod
    def validate_confidence(cls, v: float) -> float:
        from config.settings import PYDANTIC_CONFIDENCE_MIN, PYDANTIC_CONFIDENCE_MAX
        if not PYDANTIC_CONFIDENCE_MIN <= v <= PYDANTIC_CONFIDENCE_MAX:
            raise ValueError(f'Mode confidence must be between {PYDANTIC_CONFIDENCE_MIN} and {PYDANTIC_CONFIDENCE_MAX}')
        return v
    
    @field_validator('current_mode')
    @classmethod
    def validate_mode(cls, v: str) -> str:
        valid_modes = ['talk', 'expert', 'creative', 'base']
        if v not in valid_modes:
            raise ValueError(f'Invalid mode: {v}. Must be one of: {valid_modes}')
        return v
    
    @field_validator('mode_history')
    @classmethod
    def validate_mode_history_size(cls, v: List[str]) -> List[str]:
        from config.settings import PYDANTIC_MODE_HISTORY_MAX_SIZE
        if len(v) > PYDANTIC_MODE_HISTORY_MAX_SIZE:
            # Обрезаем до максимального размера
            return v[-PYDANTIC_MODE_HISTORY_MAX_SIZE:]
        return v
    
    @field_validator('cache_metrics')
    @classmethod
    def validate_cache_metrics_size(cls, v: List[float]) -> List[float]:
        from config.settings import PYDANTIC_CACHE_METRICS_MAX_SIZE
        if len(v) > PYDANTIC_CACHE_METRICS_MAX_SIZE:
            # Обрезаем до максимального размера
            return v[-PYDANTIC_CACHE_METRICS_MAX_SIZE:]
        return v


class UserSessionActor(BaseActor):
    """
    Координатор сессий пользователей.
    Управляет жизненным циклом сессий и определяет необходимость системного промпта.
    """
    
    def __init__(self):
        super().__init__("user_session", "UserSession")
        self._sessions: Dict[str, UserSession] = {}
        self._event_version_manager = EventVersionManager()
        self._last_detection_details = {}
        self._pending_requests: Dict[str, Dict[str, Any]] = {}  # Для связывания контекстных запросов
        self._cleanup_task: Optional[asyncio.Task] = None  # Задача очистки зависших запросов
        
    async def initialize(self) -> None:
        """Инициализация актора"""
        # Запускаем периодическую очистку зависших запросов
        self._cleanup_task = asyncio.create_task(self._cleanup_pending_requests_loop())
        self.logger.info("UserSessionActor initialized")
        
    async def shutdown(self) -> None:
        """Освобождение ресурсов"""
        # Останавливаем задачу очистки
        if self._cleanup_task and not self._cleanup_task.done():
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
                
        session_count = len(self._sessions)
        self._sessions.clear()
        self.logger.info(f"UserSessionActor shutdown, cleared {session_count} sessions")
        
    @measure_latency
    async def handle_message(self, message: ActorMessage) -> Optional[ActorMessage]:
        """Обработка входящих сообщений"""
        
        # Обработка USER_MESSAGE
        if message.message_type == MESSAGE_TYPES['USER_MESSAGE']:
            generate_msg = await self._handle_user_message(message)
            # Отправляем в GenerationActor
            if generate_msg and self.get_actor_system():
                await self.get_actor_system().send_message("generation", generate_msg)
            
        # Обработка метрик кэша для адаптивной стратегии
        elif message.message_type == MESSAGE_TYPES['CACHE_HIT_METRIC']:
            await self._update_cache_metrics(message)
            
        # Обработка BOT_RESPONSE для сохранения в память
        elif message.message_type == MESSAGE_TYPES['BOT_RESPONSE']:
            # Сохраняем ответ бота в память
            if self.get_actor_system():
                store_msg = ActorMessage.create(
                    sender_id=self.actor_id,
                    message_type=MESSAGE_TYPES['STORE_MEMORY'],
                    payload={
                        'user_id': message.payload['user_id'],
                        'message_type': 'bot',
                        'content': message.payload['text'],
                        'metadata': {
                            'generated_at': message.payload.get('generated_at', datetime.now().isoformat())
                        }
                    }
                )
                await self.get_actor_system().send_message("memory", store_msg)
        
        # Обработка CONTEXT_RESPONSE от MemoryActor
        elif message.message_type == MESSAGE_TYPES['CONTEXT_RESPONSE']:
            request_id = message.payload.get('request_id')
            if not request_id or request_id not in self._pending_requests:
                self.logger.warning(f"Received CONTEXT_RESPONSE with unknown request_id: {request_id}")
                return None
            
            # Извлекаем сохраненный контекст
            pending = self._pending_requests.pop(request_id)
            
            # Создаем сообщение для GenerationActor с историческим контекстом
            generate_msg = ActorMessage.create(
                sender_id=self.actor_id,
                message_type=MESSAGE_TYPES['GENERATE_RESPONSE'],
                payload={
                    'user_id': pending['user_id'],
                    'chat_id': pending['chat_id'],
                    'text': pending['text'],
                    'include_prompt': pending['include_prompt'],
                    'message_count': pending['message_count'],
                    'session_data': pending['session_data'],
                    'mode': pending['mode'],
                    'mode_confidence': pending['mode_confidence'],
                    'historical_context': message.payload.get('messages', [])  # Контекст из памяти
                }
            )
            
            self.logger.info(
                f"Created GENERATE_RESPONSE for user {pending['user_id']} "
                f"with {len(message.payload.get('messages', []))} historical messages"
            )
            
            # Отправляем в GenerationActor
            if self.get_actor_system():
                await self.get_actor_system().send_message("generation", generate_msg)
            
        return None
    
    async def _handle_user_message(self, message: ActorMessage) -> ActorMessage:
        """Обработка сообщения от пользователя"""
        user_id = message.payload['user_id']
        username = message.payload.get('username')
        text = message.payload['text']
        chat_id = message.payload['chat_id']
        
        # Получаем или создаем сессию
        session = await self._get_or_create_session(user_id, username)
        
        # Сохраняем сообщение пользователя в память
        if self.get_actor_system():
            store_msg = ActorMessage.create(
                sender_id=self.actor_id,
                message_type=MESSAGE_TYPES['STORE_MEMORY'],
                payload={
                    'user_id': user_id,
                    'message_type': 'user',
                    'content': text,
                    'metadata': {
                        'username': username,
                        'timestamp': datetime.now().isoformat()
                    }
                }
            )
            await self.get_actor_system().send_message("memory", store_msg)
        
        # Определяем режим общения
        new_mode, confidence = self._determine_generation_mode(text, session)
        
        # Проверка изменения режима
        mode_changed = False
        if new_mode != session.current_mode or session.current_mode is None:
            session.last_mode_change = datetime.now()
            session.current_mode = new_mode
            mode_changed = True
            
        # Всегда обновляем уверенность
        session.mode_confidence = confidence
        
        # Обновляем историю режимов
        from config.settings import MODE_HISTORY_SIZE
        session.mode_history.append(new_mode)
        if len(session.mode_history) > MODE_HISTORY_SIZE:
            session.mode_history.pop(0)
        
        # Логирование для отладки
        self.logger.info(
            f"Mode detection for user {user_id}: {new_mode} "
            f"(confidence: {confidence:.2f}, changed: {mode_changed})"
        )
        
        # Создаем событие если режим изменился
        if mode_changed:
            mode_event = BaseEvent.create(
                stream_id=f"user_{user_id}",
                event_type="ModeDetectedEvent",
                data={
                    "user_id": user_id,
                    "mode": new_mode,
                    "confidence": confidence,
                    "previous_mode": session.mode_history[-2] if len(session.mode_history) > 1 else None,
                    "detection_details": getattr(self, '_last_detection_details', {}),
                    "timestamp": datetime.now().isoformat()
                }
            )
            await self._append_event(mode_event)
        
        # Обновляем счетчики
        session.message_count += 1
        session.last_activity = datetime.now()
        
        # Определяем необходимость системного промпта
        include_prompt = self._should_include_prompt(session)
        
        # Логируем решение о промпте
        if include_prompt:
            prompt_event = BaseEvent.create(
                stream_id=f"user_{user_id}",
                event_type="PromptInclusionEvent",
                data={
                    "user_id": user_id,
                    "message_count": session.message_count,
                    "strategy": PROMPT_CONFIG["prompt_strategy"],
                    "reason": self._get_prompt_reason(session)
                }
            )
            await self._append_event(prompt_event)
        
        # Сохраняем контекст генерации для последующего использования
        request_id = str(uuid.uuid4())
        self._pending_requests[request_id] = {
            'user_id': user_id,
            'chat_id': chat_id,
            'text': text,
            'include_prompt': include_prompt,
            'message_count': session.message_count,
            'session_data': {
                'username': session.username,
                'created_at': session.created_at.isoformat()
            },
            'mode': session.current_mode,
            'mode_confidence': session.mode_confidence,
            'timestamp': datetime.now()
        }
        
        # Запрашиваем исторический контекст из MemoryActor
        get_context_msg = ActorMessage.create(
            sender_id=self.actor_id,
            message_type=MESSAGE_TYPES['GET_CONTEXT'],
            payload={
                'user_id': user_id,
                'request_id': request_id,
                'limit': STM_CONTEXT_SIZE_FOR_GENERATION,
                'format_type': 'structured'  # Для DeepSeek API
            },
            reply_to=self.actor_id  # Ответ нужен нам
        )
        
        await self.get_actor_system().send_message("memory", get_context_msg)
        self.logger.info(f"Requested context for user {user_id}, request_id: {request_id}")
        
        # НЕ возвращаем сообщение - ждем CONTEXT_RESPONSE
        return None
    
    async def _get_or_create_session(self, user_id: str, username: Optional[str]) -> UserSession:
        """Получить существующую или создать новую сессию"""
        if user_id not in self._sessions:
            session = UserSession(user_id=user_id, username=username)
            self._sessions[user_id] = session
            
            # Событие о создании сессии
            event = BaseEvent.create(
                stream_id=f"user_{user_id}",
                event_type="SessionCreatedEvent",
                data={
                    "user_id": user_id,
                    "username": username,
                    "created_at": session.created_at.isoformat()
                }
            )
            
            # Сохраняем событие
            await self._append_event(event)
            
            self.logger.info(f"Created new session for user {user_id}")
        
        return self._sessions[user_id]
    
    def _should_include_prompt(self, session: UserSession) -> bool:
        """Определить необходимость включения системного промпта"""
        strategy = PROMPT_CONFIG["prompt_strategy"]
        
        if not PROMPT_CONFIG["enable_periodic_prompt"]:
            return True  # Всегда включать если периодичность отключена
            
        if strategy == "always":
            return True
            
        elif strategy == "periodic":
            # Каждое N-ое сообщение
            interval = PROMPT_CONFIG["system_prompt_interval"]
            return session.message_count % interval == 1
            
        elif strategy == "adaptive":
            # Адаптивная стратегия на основе метрик
            if session.message_count % PROMPT_CONFIG["system_prompt_interval"] == 1:
                return True  # Базовая периодичность
                
            # Проверяем метрики кэша
            if len(session.cache_metrics) >= 5:
                avg_cache_hit = sum(session.cache_metrics[-5:]) / 5
                if avg_cache_hit < PROMPT_CONFIG["cache_hit_threshold"]:
                    # Cache hit rate слишком низкий, включаем промпт
                    return True
                    
        return False
    
    def _get_prompt_reason(self, session: UserSession) -> str:
        """Получить причину включения промпта для логирования"""
        strategy = PROMPT_CONFIG["prompt_strategy"]
        
        if strategy == "always":
            return "always_strategy"
        elif strategy == "periodic":
            return f"periodic_interval_{PROMPT_CONFIG['system_prompt_interval']}"
        elif strategy == "adaptive":
            if len(session.cache_metrics) >= 5:
                avg_cache_hit = sum(session.cache_metrics[-5:]) / 5
                if avg_cache_hit < PROMPT_CONFIG["cache_hit_threshold"]:
                    return f"low_cache_hit_rate_{avg_cache_hit:.2f}"
            return "adaptive_periodic"
        
        return "unknown"
    
    async def _update_cache_metrics(self, message: ActorMessage) -> None:
        """Обновить метрики кэша для адаптивной стратегии"""
        user_id = message.payload.get('user_id')
        if not user_id or user_id not in self._sessions:
            return
            
        session = self._sessions[user_id]
        cache_hit_rate = message.payload.get('cache_hit_rate', 0.0)
        
        # Сохраняем метрику
        session.cache_metrics.append(cache_hit_rate)
        
        # Ограничиваем размер истории
        if len(session.cache_metrics) > 20:
            session.cache_metrics = session.cache_metrics[-20:]
    
    def _determine_generation_mode(
        self, 
        text: str, 
        session: UserSession
    ) -> Tuple[str, float]:
        """
        Определяет режим генерации на основе текста сообщения.
        
        Args:
            text: Текст сообщения пользователя
            session: Текущая сессия пользователя
            
        Returns:
            (режим, уверенность) - режим из GENERATION_MODES и уверенность 0-1
        """
        from config.prompts import MODE_DETECTION_CONFIG
        from config.settings import (
            MODE_CONFIDENCE_THRESHOLD, 
            MODE_SCORE_NORMALIZATION_FACTOR,
            CONTEXTUAL_PATTERN_PHRASE_WEIGHT,
            CONTEXTUAL_PATTERN_DOMAIN_WEIGHT,
            CONTEXTUAL_PATTERN_CONTEXT_MULTIPLIER,
            CONTEXTUAL_PATTERN_SUPPRESSOR_MULTIPLIER,
            MODE_DETECTION_DEBUG_LOGGING
        )
        
        # Базовая проверка
        if not text or len(text) < MODE_DETECTION_CONFIG["min_text_length"]:
            return session.current_mode or 'talk', 0.5
        
        text_lower = text.lower()
        
        # Подсчет очков для каждого режима
        scores = {
            'expert': 0,
            'creative': 0,
            'talk': 0
        }
        
        # Детали определения для логирования
        detection_details = {
            'expert': {'patterns': [], 'score': 0},
            'creative': {'patterns': [], 'score': 0},
            'talk': {'patterns': [], 'score': 0}
        }
        
        # Получаем веса
        weights = MODE_DETECTION_CONFIG["mode_weights"]
        
        # НОВАЯ ЛОГИКА: Проверка контекстных паттернов
        contextual_patterns = MODE_DETECTION_CONFIG.get("contextual_patterns", {})
        
        if contextual_patterns:
            for mode in ['expert', 'creative', 'talk']:
                if mode not in contextual_patterns:
                    continue
                    
                mode_patterns = contextual_patterns[mode]
                
                # Уровень 1: Точные фразы
                for phrase in mode_patterns.get("exact_phrases", []):
                    if phrase in text_lower:
                        phrase_score = weights[mode] * CONTEXTUAL_PATTERN_PHRASE_WEIGHT
                        scores[mode] += phrase_score
                        detection_details[mode]['patterns'].append(f"exact_phrase: {phrase}")
                        
                # Уровень 2: Контекстные слова
                for word, modifiers in mode_patterns.get("contextual_words", {}).items():
                    if word in text_lower:
                        # Базовый вес слова
                        word_score = weights[mode]
                        
                        # Проверяем усилители
                        for enhancer in modifiers.get("enhancers", []):
                            if enhancer in text_lower:
                                word_score *= CONTEXTUAL_PATTERN_CONTEXT_MULTIPLIER
                                detection_details[mode]['patterns'].append(f"enhanced: {word}+{enhancer}")
                                break
                        
                        # Проверяем подавители
                        suppressed = False
                        for suppressor in modifiers.get("suppressors", []):
                            if suppressor in text_lower:
                                word_score *= CONTEXTUAL_PATTERN_SUPPRESSOR_MULTIPLIER
                                suppressed = True
                                detection_details[mode]['patterns'].append(f"suppressed: {word}-{suppressor}")
                                break
                                
                        # Прерываем добавление очков, если suppressor полностью обнуляет
                        if suppressed and CONTEXTUAL_PATTERN_SUPPRESSOR_MULTIPLIER == 0:
                            continue  # подавитель отключил режим полностью
                        
                        # Добавляем очки всегда, подавители уже учтены в word_score
                        scores[mode] += word_score
                            
                # Уровень 3: Доменные маркеры
                domain_count = 0
                for marker in mode_patterns.get("domain_markers", []):
                    if marker in text_lower:
                        domain_count += 1
                        
                if domain_count > 0:
                    # Логарифмическая шкала для доменных маркеров, чтобы много маркеров не давали слишком высокий score
                    import math
                    domain_score = weights[mode] * CONTEXTUAL_PATTERN_DOMAIN_WEIGHT * (1 + math.log(domain_count))
                    scores[mode] += domain_score
                    detection_details[mode]['patterns'].append(f"domains: {domain_count}")
        
        # СТАРАЯ ЛОГИКА: Простые паттерны (fallback)
        if all(score == 0 for score in scores.values()):
            if MODE_DETECTION_DEBUG_LOGGING:
                self.logger.debug("[fallback] all scores are zero, applying simple pattern fallback")
        
            # Получаем паттерны из конфига
            expert_patterns = MODE_DETECTION_CONFIG["expert_patterns"]
            creative_patterns = MODE_DETECTION_CONFIG["creative_patterns"]
            talk_patterns = MODE_DETECTION_CONFIG["talk_patterns"]
            
            # Подсчет совпадений с учетом весов
            for pattern in expert_patterns:
                if pattern in text_lower:
                    scores['expert'] += weights['expert']
                    detection_details['expert']['patterns'].append(f"simple: {pattern}")
                    
            for pattern in creative_patterns:
                if pattern in text_lower:
                    scores['creative'] += weights['creative']
                    detection_details['creative']['patterns'].append(f"simple: {pattern}")
                    
            for pattern in talk_patterns:
                if pattern in text_lower:
                    scores['talk'] += weights['talk']
                    detection_details['talk']['patterns'].append(f"simple: {pattern}")
        
        # Вопросительные слова усиливают expert
        question_words = MODE_DETECTION_CONFIG["question_words"]
        question_bonus = MODE_DETECTION_CONFIG["question_bonus"]
        
        if any(q in text_lower for q in question_words):
            scores['expert'] += question_bonus
            detection_details['expert']['patterns'].append("question_bonus")
        
        # Сохраняем финальные очки
        for mode in scores:
            detection_details[mode]['score'] = scores[mode]
        
        # Определение режима с максимальным счетом
        max_score = max(scores.values())
        if max_score == 0:
            detected_mode = 'talk'
            confidence = MODE_CONFIDENCE_THRESHOLD
        else:
            detected_mode = max(scores, key=scores.get)
            confidence = min(max_score / MODE_SCORE_NORMALIZATION_FACTOR, 1.0)
        
        # Учет истории (если последние 3 сообщения в одном режиме)
        if len(session.mode_history) >= 3:
            last_modes = session.mode_history[-3:]
            if all(m == last_modes[0] for m in last_modes):
                if detected_mode == last_modes[0]:
                    multiplier = MODE_DETECTION_CONFIG["stable_history_multiplier"]
                    confidence = min(confidence * multiplier, 1.0)
                    detection_details[detected_mode]['patterns'].append("history_boost")
        
        # Логирование деталей если включено
        if MODE_DETECTION_DEBUG_LOGGING and self.logger.isEnabledFor(logging.DEBUG):
            self.logger.debug(
                f"Mode detection details for '{text[:50]}...': "
                f"winner={detected_mode} ({confidence:.2f}), "
                f"scores={scores}, "
                f"details={detection_details}"
            )
            
            # Создаем событие для отладки
            if hasattr(self, '_event_version_manager'):
                debug_event = BaseEvent.create(
                    stream_id=f"debug_mode_{session.user_id}",
                    event_type="PatternDebugEvent",
                    data={
                        "user_id": session.user_id,
                        "text_preview": text[:100],
                        "detected_mode": detected_mode,
                        "confidence": confidence,
                        "scores": scores,
                        "detection_details": detection_details,
                        "timestamp": datetime.now().isoformat()
                    }
                )
                # Используем create_task чтобы не блокировать
                asyncio.create_task(self._append_event(debug_event))
        
        # Сохраняем детали для использования в событиях
        self._last_detection_details = detection_details
        
        return detected_mode, confidence
    
    async def _cleanup_pending_requests_loop(self) -> None:
        """Периодическая очистка зависших запросов"""
        while self.is_running:
            try:
                await asyncio.sleep(10)  # Проверка каждые 10 секунд
                await self._cleanup_expired_requests()
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"Error in cleanup loop: {str(e)}")
    
    async def _cleanup_expired_requests(self) -> None:
        """Очистка запросов старше таймаута"""
        now = datetime.now()
        expired = []
        
        for request_id, data in self._pending_requests.items():
            if (now - data['timestamp']).total_seconds() > STM_CONTEXT_REQUEST_TIMEOUT:
                expired.append(request_id)
        
        for request_id in expired:
            pending = self._pending_requests.pop(request_id)
            self.logger.warning(
                f"Context request timeout for user {pending['user_id']}, "
                f"generating without historical context"
            )
            
            # Генерируем без исторического контекста как fallback
            generate_msg = ActorMessage.create(
                sender_id=self.actor_id,
                message_type=MESSAGE_TYPES['GENERATE_RESPONSE'],
                payload={
                    'user_id': pending['user_id'],
                    'chat_id': pending['chat_id'],
                    'text': pending['text'],
                    'include_prompt': pending['include_prompt'],
                    'message_count': pending['message_count'],
                    'session_data': pending['session_data'],
                    'mode': pending['mode'],
                    'mode_confidence': pending['mode_confidence'],
                    'historical_context': []  # Пустой контекст при таймауте
                }
            )
            
            if self.get_actor_system():
                await self.get_actor_system().send_message("generation", generate_msg)
    
    async def _append_event(self, event: BaseEvent) -> None:
        """Добавить событие через менеджер версий"""
        await self._event_version_manager.append_event(event, self.get_actor_system())
    
    async def _append_event(self, event: BaseEvent) -> None:
        """Добавить событие через менеджер версий"""
        await self._event_version_manager.append_event(event, self.get_actor_system())