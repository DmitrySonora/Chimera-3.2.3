import asyncio
import pytest
from datetime import datetime
import os

from actors.actor_system import ActorSystem
from actors.user_session_actor import UserSessionActor
from actors.generation_actor import GenerationActor
from actors.memory_actor import MemoryActor
from actors.telegram_actor import TelegramInterfaceActor
from actors.messages import ActorMessage, MESSAGE_TYPES
from config.logging import setup_logging

# Настраиваем логирование
setup_logging()

# Пропускаем тесты если нет конфигурации
pytestmark = pytest.mark.skipif(
    not os.getenv("DEEPSEEK_API_KEY") and not os.getenv("TELEGRAM_BOT_TOKEN"),
    reason="Integration tests require API keys"
)


@pytest.mark.asyncio
async def test_full_memory_cycle_with_real_actors():
    """Тест полного цикла с реальными акторами"""
    system = ActorSystem("test_memory")
    
    # Создаем Event Store
    await system.create_and_set_event_store()
    
    # Создаем реальные акторы
    session_actor = UserSessionActor()
    memory_actor = MemoryActor()
    generation_actor = GenerationActor()
    
    # Регистрируем
    await system.register_actor(session_actor)
    await system.register_actor(memory_actor)
    await system.register_actor(generation_actor)
    
    # Вместо реального Telegram используем заглушку
    class TestTelegramActor(TelegramInterfaceActor):
        def __init__(self):
            super().__init__()
            self.collected_responses = []
            
        async def _send_bot_response(self, message: ActorMessage) -> None:
            """Перехватываем ответы вместо отправки в Telegram"""
            self.collected_responses.append({
                'user_id': message.payload['user_id'],
                'text': message.payload['text'],
                'timestamp': datetime.now()
            })
            # Все равно отправляем в UserSessionActor для сохранения
            if self.get_actor_system():
                await self.get_actor_system().send_message("user_session", message)
    
    telegram_test = TestTelegramActor()
    await system.register_actor(telegram_test)
    
    # Запускаем систему
    await system.start()
    
    try:
        # Тест 1: Отправляем первое сообщение
        msg1 = ActorMessage.create(
            sender_id="test",
            message_type=MESSAGE_TYPES['USER_MESSAGE'],
            payload={
                'user_id': 'test_user_integration',
                'chat_id': 12345,
                'username': 'TestUser',
                'text': 'Привет, меня зовут Алексей и я люблю физику'
            }
        )
        await system.send_message("user_session", msg1)
        
        # Ждем полной обработки
        await asyncio.sleep(3)
        
        # Тест 2: Отправляем второе сообщение
        msg2 = ActorMessage.create(
            sender_id="test",
            message_type=MESSAGE_TYPES['USER_MESSAGE'],
            payload={
                'user_id': 'test_user_integration',
                'chat_id': 12345,
                'username': 'TestUser',
                'text': 'Расскажи что-нибудь интересное'
            }
        )
        await system.send_message("user_session", msg2)
        
        # Ждем генерации с контекстом
        await asyncio.sleep(15)  # DeepSeek может быть медленным
        
        # Проверяем результаты
        assert len(telegram_test.collected_responses) >= 2, "Должно быть минимум 2 ответа"
        
        # Проверяем, что второй ответ учитывает контекст
        second_response = telegram_test.collected_responses[1]['text'].lower()
        # Бот должен помнить имя или интерес к физике
        context_found = any(word in second_response for word in ['алексей', 'физик'])
        print(f"Second response: {telegram_test.collected_responses[1]['text'][:200]}...")
        assert context_found, "Бот должен помнить контекст из первого сообщения"
        
        # Проверяем события в Event Store
        if system._event_store:
            # События памяти
            memory_events = await system._event_store.get_stream("memory_test_user_integration")
            assert len(memory_events) > 0, "Должны быть события памяти"
            
            event_types = [e.event_type for e in memory_events]
            assert "MemoryStoredEvent" in event_types, "Должны быть события сохранения"
            assert "ContextRetrievedEvent" in event_types, "Должны быть события получения контекста"
            
            # Проверяем метрики производительности
            context_events = [e for e in memory_events if e.event_type == "ContextRetrievedEvent"]
            if context_events:
                last_context_event = context_events[-1]
                retrieval_time = last_context_event.data.get('retrieval_time_ms', 0)
                print(f"Context retrieval time: {retrieval_time}ms")
                assert retrieval_time > 0, "Должно быть измерено время получения контекста"
        
    finally:
        await system.stop()


@pytest.mark.asyncio
async def test_memory_persistence_across_sessions():
    """Тест сохранения памяти между сессиями"""
    # Первая сессия - сохраняем
    system1 = ActorSystem("test_persistence_1")
    await system1.create_and_set_event_store()
    
    memory1 = MemoryActor()
    await system1.register_actor(memory1)
    await system1.start()
    
    try:
        # Сохраняем несколько сообщений
        for i in range(3):
            store_msg = ActorMessage.create(
                sender_id="test",
                message_type=MESSAGE_TYPES['STORE_MEMORY'],
                payload={
                    'user_id': 'persistence_test_user',
                    'message_type': 'user' if i % 2 == 0 else 'bot',
                    'content': f'Сообщение номер {i+1}',
                    'metadata': {'session': 1, 'index': i}
                }
            )
            await system1.send_message("memory", store_msg)
            await asyncio.sleep(0.1)
        
    finally:
        await system1.stop()
    
    # Вторая сессия - проверяем что данные сохранились
    system2 = ActorSystem("test_persistence_2")
    await system2.create_and_set_event_store()
    
    memory2 = MemoryActor()
    await system2.register_actor(memory2)
    await system2.start()
    
    try:
        # Запрашиваем контекст
        context = await memory2.get_context(
            user_id='persistence_test_user',
            limit=10,
            format_type='text'
        )
        
        # Проверяем что сообщения на месте
        assert context.total_messages == 3, f"Должно быть 3 сообщения, найдено {context.total_messages}"
        assert context.messages[0]['content'] == 'Сообщение номер 1'
        assert context.messages[2]['content'] == 'Сообщение номер 3'
        
        print(f"Successfully retrieved {context.total_messages} messages from previous session")
        
    finally:
        await system2.stop()


@pytest.mark.asyncio
async def test_memory_circular_buffer():
    """Тест кольцевого буфера - старые сообщения удаляются"""
    from config.settings import STM_BUFFER_SIZE
    
    system = ActorSystem("test_circular")
    await system.create_and_set_event_store()
    
    memory = MemoryActor()
    await system.register_actor(memory)
    await system.start()
    
    try:
        test_user = 'circular_buffer_test_user'
        
        # Сохраняем больше сообщений чем размер буфера
        messages_to_store = STM_BUFFER_SIZE + 10
        
        for i in range(messages_to_store):
            await memory.store_interaction(
                user_id=test_user,
                message_type='user',
                content=f'Message {i}',
                metadata={'index': i}
            )
            # Небольшая задержка чтобы не перегружать БД
            if i % 10 == 0:
                await asyncio.sleep(0.1)
        
        # Получаем контекст
        context = await memory.get_context(
            user_id=test_user,
            limit=STM_BUFFER_SIZE * 2,  # Запрашиваем больше чем есть
            format_type='text'
        )
        
        # Проверяем что осталось только STM_BUFFER_SIZE сообщений
        assert context.total_messages == STM_BUFFER_SIZE, \
            f"Должно остаться {STM_BUFFER_SIZE} сообщений, найдено {context.total_messages}"
        
        # Проверяем что остались самые новые сообщения
        first_message_index = messages_to_store - STM_BUFFER_SIZE
        assert context.messages[0]['content'] == f'Message {first_message_index}'
        assert context.messages[-1]['content'] == f'Message {messages_to_store - 1}'
        
        print(f"Circular buffer works: kept last {STM_BUFFER_SIZE} messages")
        
    finally:
        await system.stop()


@pytest.mark.asyncio
async def test_memory_degraded_mode():
    """Тест работы в degraded mode при недоступной БД"""
    system = ActorSystem("test_degraded")
    
    # НЕ создаем Event Store чтобы спровоцировать degraded mode
    memory = MemoryActor()
    # Принудительно отключаем БД
    memory._pool = None
    memory._degraded_mode = True
    
    await system.register_actor(memory)
    await system.start()
    
    try:
        # Пытаемся сохранить - должно просто залогироваться
        store_msg = ActorMessage.create(
            sender_id="test",
            message_type=MESSAGE_TYPES['STORE_MEMORY'],
            payload={
                'user_id': 'degraded_test_user',
                'message_type': 'user',
                'content': 'This should not crash',
                'metadata': {}
            }
        )
        await system.send_message("memory", store_msg)
        
        # Небольшая задержка
        await asyncio.sleep(0.5)
        
        # Система не должна упасть
        assert memory.is_running, "MemoryActor должен продолжать работать"
        assert memory._metrics['store_memory_count'] == 1, "Счетчик должен увеличиться"
        
        print("Degraded mode works correctly - no crash on store")
        
    finally:
        await system.stop()


@pytest.mark.asyncio
async def test_context_request_timeout():
    """Тест обработки таймаута контекста в UserSessionActor"""
    from config.settings import STM_CONTEXT_REQUEST_TIMEOUT
    
    # Временно устанавливаем короткий таймаут
    import config.settings
    original_timeout = config.settings.STM_CONTEXT_REQUEST_TIMEOUT
    config.settings.STM_CONTEXT_REQUEST_TIMEOUT = 1.0  # 1 секунда
    
    system = ActorSystem("test_timeout")
    
    try:
        # Создаем UserSessionActor но НЕ создаем MemoryActor
        session_actor = UserSessionActor()
        await system.register_actor(session_actor)
        
        # Фейковый GenerationActor для проверки
        from actors.base_actor import BaseActor
        
        class TimeoutTestActor(BaseActor):
            def __init__(self):
                super().__init__("generation", "TimeoutTest")
                self.received_messages = []
                
            async def initialize(self):
                pass
                
            async def shutdown(self):
                pass
                
            async def handle_message(self, message: ActorMessage):
                if message.message_type == MESSAGE_TYPES['GENERATE_RESPONSE']:
                    self.received_messages.append(message)
                return None
        
        test_gen = TimeoutTestActor()
        await system.register_actor(test_gen)
        
        await system.start()
        
        # Отправляем сообщение
        user_msg = ActorMessage.create(
            sender_id="test",
            message_type=MESSAGE_TYPES['USER_MESSAGE'],
            payload={
                'user_id': 'timeout_test_user',
                'chat_id': 12345,
                'username': 'TimeoutTest',
                'text': 'Test message for timeout'
            }
        )
        await system.send_message("user_session", user_msg)
        
        # Ждем больше таймаута
        await asyncio.sleep(2)
        
        # Проверяем что сообщение все равно дошло до GenerationActor
        assert len(test_gen.received_messages) == 1, "Должно быть получено сообщение после таймаута"
        
        # Проверяем что контекст пустой из-за таймаута
        msg = test_gen.received_messages[0]
        assert msg.payload.get('historical_context') == [], "Контекст должен быть пустым после таймаута"
        
        print("Timeout handling works - generation proceeds without context")
        
    finally:
        # Восстанавливаем оригинальный таймаут
        config.settings.STM_CONTEXT_REQUEST_TIMEOUT = original_timeout
        await system.stop()


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])