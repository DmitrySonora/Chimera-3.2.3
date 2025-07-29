from typing import Optional, Dict, Any
import asyncio
import aiohttp
from datetime import datetime

from actors.base_actor import BaseActor
from actors.messages import ActorMessage, MESSAGE_TYPES
from config.messages import USER_MESSAGES
from config.settings import (
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_POLLING_TIMEOUT,
    TELEGRAM_TYPING_UPDATE_INTERVAL,
    TELEGRAM_MAX_MESSAGE_LENGTH,
    TELEGRAM_TYPING_CLEANUP_THRESHOLD,
    TELEGRAM_API_DEFAULT_TIMEOUT,
    TELEGRAM_MAX_TYPING_TASKS
)
from utils.monitoring import measure_latency


class TelegramInterfaceActor(BaseActor):
    """
    Интерфейс между Telegram Bot API и Actor System.
    Обрабатывает входящие сообщения и отправляет ответы.
    """
    
    def __init__(self):
        super().__init__("telegram", "Telegram")
        self._session: Optional[aiohttp.ClientSession] = None
        self._update_offset = 0
        self._polling_task: Optional[asyncio.Task] = None
        self._typing_tasks: Dict[int, asyncio.Task] = {}  # chat_id -> task
        self._base_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
        self._typing_cleanup_counter = 0
        
    async def initialize(self) -> None:
        """Инициализация HTTP сессии и запуск polling"""
        if not TELEGRAM_BOT_TOKEN:
            raise ValueError("TELEGRAM_BOT_TOKEN not set in config/settings.py")
            
        self._session = aiohttp.ClientSession()
        
        # Проверяем токен
        me = await self._api_call("getMe")
        self.logger.info(f"Connected as @{me['result']['username']}")
        
        # Запускаем polling
        self._polling_task = asyncio.create_task(self._polling_loop())
        
        self.logger.info("TelegramInterfaceActor initialized")
        
    async def shutdown(self) -> None:
        """Остановка polling и освобождение ресурсов"""
        # Останавливаем polling
        if self._polling_task:
            self._polling_task.cancel()
            try:
                await self._polling_task
            except asyncio.CancelledError:
                pass
                
        # Останавливаем все typing индикаторы
        for task in self._typing_tasks.values():
            task.cancel()
            
        # Очищаем словарь
        self._typing_tasks.clear()
        
        # Закрываем HTTP сессию
        if self._session:
            await self._session.close()
            
        self.logger.info("TelegramInterfaceActor shutdown")
        
    @measure_latency
    async def handle_message(self, message: ActorMessage) -> Optional[ActorMessage]:
        """Обработка сообщений от других акторов"""
        
        # Новое сообщение от Telegram для обработки
        if message.message_type == MESSAGE_TYPES['PROCESS_USER_MESSAGE']:
            # Извлекаем данные и отправляем в UserSessionActor
            user_msg = ActorMessage.create(
                sender_id=self.actor_id,
                message_type=MESSAGE_TYPES['USER_MESSAGE'],
                payload=message.payload
            )
            
            # Отправляем в UserSessionActor через ActorSystem
            if self.get_actor_system():
                await self.get_actor_system().send_message("user_session", user_msg)
            
        # Ответ бота готов
        elif message.message_type == MESSAGE_TYPES['BOT_RESPONSE']:
            await self._send_bot_response(message)
            
        # Ошибка генерации
        elif message.message_type == MESSAGE_TYPES['ERROR']:
            await self._send_error_message(message)
            
        # Streaming чанк (для будущего)
        elif message.message_type == MESSAGE_TYPES['STREAMING_CHUNK']:
            # TODO: Реализовать в следующих этапах
            pass
            
        return None
    
    async def _polling_loop(self) -> None:
        """Основной цикл получения обновлений от Telegram"""
        self.logger.info("Started Telegram polling")
        
        while self.is_running:
            try:
                # Получаем обновления
                updates = await self._get_updates()
                
                # Обрабатываем каждое обновление
                for update in updates:
                    await self._process_update(update)
                    
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"Polling error: {str(e)}")
                await asyncio.sleep(5)  # Пауза перед переподключением
                
        self.logger.info("Stopped Telegram polling")
    
    async def _get_updates(self) -> list:
        """Получение обновлений через long polling"""
        try:
            result = await self._api_call(
                "getUpdates",
                params={
                    "offset": self._update_offset,
                    "timeout": TELEGRAM_POLLING_TIMEOUT,
                    "allowed_updates": ["message"]
                },
                timeout=TELEGRAM_POLLING_TIMEOUT + 5
            )
            
            updates = result.get("result", [])
            
            # Обновляем offset
            if updates:
                self._update_offset = updates[-1]["update_id"] + 1
                
            return updates
            
        except asyncio.TimeoutError:
            return []  # Нормальная ситуация для long polling
        except Exception as e:
            self.logger.error(f"Failed to get updates: {str(e)}")
            return []
    
    async def _process_update(self, update: Dict[str, Any]) -> None:
        """Обработка одного обновления от Telegram"""
        # Извлекаем сообщение
        message = update.get("message")
        if not message:
            return
            
        # Извлекаем данные
        chat_id = message["chat"]["id"]
        user_id = message["from"]["id"]
        username = message["from"].get("username")
        text = message.get("text", "")
        
        # Игнорируем не-текстовые сообщения пока
        if not text:
            return
            
        # Обработка команд
        if text.startswith("/"):
            await self._handle_command(chat_id, text)
            return
            
        # Запускаем typing индикатор
        await self._start_typing(chat_id)
        
        # Создаем сообщение для обработки через Actor System
        process_msg = ActorMessage.create(
            sender_id=self.actor_id,
            message_type=MESSAGE_TYPES['PROCESS_USER_MESSAGE'],
            payload={
                'user_id': str(user_id),
                'chat_id': chat_id,
                'username': username,
                'text': text,
                'timestamp': datetime.now().isoformat()
            }
        )
        
        # Отправляем себе же для обработки через Actor System
        if self.get_actor_system():
            await self.get_actor_system().send_message(self.actor_id, process_msg)
        
        self.logger.debug(f"Queued message from user {user_id}: {text[:50]}...")
    
    async def _handle_command(self, chat_id: int, command: str) -> None:
        """Обработка команд бота"""
        if command == "/start":
            from config.settings import DAILY_MESSAGE_LIMIT
            welcome_text = USER_MESSAGES["welcome"].format(
                DAILY_MESSAGE_LIMIT=DAILY_MESSAGE_LIMIT
            )
            await self._send_message(chat_id, welcome_text)
        else:
            await self._send_message(chat_id, USER_MESSAGES["unknown_command"])
    
    async def _send_bot_response(self, message: ActorMessage) -> None:
        """Отправка ответа бота пользователю"""
        chat_id = message.payload['chat_id']
        text = message.payload['text']
        
        # Останавливаем typing
        await self._stop_typing(chat_id)
        
        # Отправляем сообщение
        await self._send_message(chat_id, text)
        
        # Пересылаем BOT_RESPONSE в UserSessionActor для сохранения в память
        if self.get_actor_system():
            await self.get_actor_system().send_message("user_session", message)
    
    async def _send_error_message(self, message: ActorMessage) -> None:
        """Отправка сообщения об ошибке"""
        chat_id = message.payload['chat_id']
        error_type = message.payload.get('error_type', 'api_error')
        
        # Останавливаем typing
        await self._stop_typing(chat_id)
        
        # Выбираем сообщение
        error_text = USER_MESSAGES.get(error_type, USER_MESSAGES["api_error"])
        
        await self._send_message(chat_id, error_text)
    
    async def _send_message(self, chat_id: int, text: str) -> None:
        """Отправка сообщения в Telegram"""
        # Разбиваем длинные сообщения
        chunks = self._split_long_message(text)
        
        for chunk in chunks:
            try:
                await self._api_call(
                    "sendMessage",
                    data={
                        "chat_id": chat_id,
                        "text": chunk,
                        "parse_mode": "Markdown"
                    }
                )
            except Exception as e:
                self.logger.error(f"Failed to send message to {chat_id}: {str(e)}")
                # Пробуем без Markdown
                try:
                    await self._api_call(
                        "sendMessage",
                        data={
                            "chat_id": chat_id,
                            "text": chunk
                        }
                    )
                except Exception as e2:
                    self.logger.error(f"Failed to send plain message: {str(e2)}")
    
    def _split_long_message(self, text: str) -> list:
        """Разбивка длинного сообщения на части"""
        if len(text) <= TELEGRAM_MAX_MESSAGE_LENGTH:
            return [text]
            
        # Разбиваем по параграфам
        chunks = []
        current = ""
        
        for paragraph in text.split("\n\n"):
            if len(current) + len(paragraph) + 2 > TELEGRAM_MAX_MESSAGE_LENGTH:
                if current:
                    chunks.append(current.strip())
                current = paragraph
            else:
                if current:
                    current += "\n\n"
                current += paragraph
                
        if current:
            chunks.append(current.strip())
            
        return chunks
    
    async def _start_typing(self, chat_id: int) -> None:
        """Запуск typing индикатора"""
        # Останавливаем предыдущий если есть
        await self._stop_typing(chat_id)
        
        # Периодическая очистка завершенных задач
        self._typing_cleanup_counter += 1
        if self._typing_cleanup_counter >= TELEGRAM_TYPING_CLEANUP_THRESHOLD:
            self._cleanup_typing_tasks()
            self._typing_cleanup_counter = 0
        
        # Проверяем лимит активных typing задач
        if len(self._typing_tasks) >= TELEGRAM_MAX_TYPING_TASKS:
            self.logger.warning(
                f"Typing tasks limit reached ({TELEGRAM_MAX_TYPING_TASKS}), "
                f"forcing cleanup"
            )
            # Принудительная очистка всех завершенных задач
            self._cleanup_typing_tasks()
            
            # Если все еще превышен лимит, удаляем самые старые
            if len(self._typing_tasks) >= TELEGRAM_MAX_TYPING_TASKS:
                # Удаляем первые 10% задач (самые старые)
                to_remove = max(1, len(self._typing_tasks) // 10)
                for _ in range(to_remove):
                    oldest_chat_id = next(iter(self._typing_tasks))
                    self._typing_tasks[oldest_chat_id].cancel()
                    del self._typing_tasks[oldest_chat_id]
                self.logger.warning(f"Forcefully removed {to_remove} oldest typing tasks")
        
        # Создаем новую задачу
        self._typing_tasks[chat_id] = asyncio.create_task(
            self._typing_loop(chat_id)
        )
    
    async def _stop_typing(self, chat_id: int) -> None:
        """Остановка typing индикатора"""
        if chat_id in self._typing_tasks:
            self._typing_tasks[chat_id].cancel()
            del self._typing_tasks[chat_id]
    
    async def _typing_loop(self, chat_id: int) -> None:
        """Цикл обновления typing индикатора"""
        try:
            while True:
                await self._api_call(
                    "sendChatAction",
                    data={
                        "chat_id": chat_id,
                        "action": "typing"
                    }
                )
                await asyncio.sleep(TELEGRAM_TYPING_UPDATE_INTERVAL)
        except asyncio.CancelledError:
            pass
    
    def _cleanup_typing_tasks(self) -> None:
        """Очистка завершенных typing задач из словаря"""
        completed_chats = []
        for chat_id, task in self._typing_tasks.items():
            if task.done():
                completed_chats.append(chat_id)
        
        for chat_id in completed_chats:
            del self._typing_tasks[chat_id]
            
        if completed_chats:
            self.logger.debug(f"Cleaned {len(completed_chats)} completed typing tasks")
    
    async def _api_call(
        self, 
        method: str, 
        data: Optional[Dict] = None,
        params: Optional[Dict] = None,
        timeout: Optional[int] = None
    ) -> Dict:
        """Базовый метод для вызова Telegram API"""
        url = f"{self._base_url}/{method}"
        
        async with self._session.post(
            url,
            json=data,
            params=params,
            timeout=timeout or TELEGRAM_API_DEFAULT_TIMEOUT
        ) as response:
            result = await response.json()
            
            if not result.get("ok"):
                raise Exception(f"Telegram API error: {result}")
                
            return result