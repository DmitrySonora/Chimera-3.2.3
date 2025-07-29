"""
Интеграционные тесты для кольцевого буфера STM в MemoryActor
"""
import pytest
import asyncio
from datetime import datetime

from actors.actor_system import ActorSystem
from actors.memory_actor import MemoryActor
from actors.messages import ActorMessage, MESSAGE_TYPES
from actors.events import EventStore
from config.logging import setup_logging
from config.settings import STM_BUFFER_SIZE, STM_MESSAGE_MAX_LENGTH

# Настраиваем логирование для тестов
setup_logging()


@pytest.mark.asyncio
class TestMemoryBuffer:
    """Тесты кольцевого буфера STM через Actor Model"""
    
    async def test_buffer_maintains_50_messages(self):
        """Тест: буфер хранит только последние 50 сообщений"""
        # 1. Создать систему и актора
        system = ActorSystem("test-buffer")
        memory_actor = MemoryActor()
        event_store = EventStore()
        
        system.set_event_store(event_store)
        await system.register_actor(memory_actor)
        await system.start()
        
        try:
            # 2. Отправить 60 сообщений ЧЕРЕЗ АКТОРА
            test_user = "test_user_buffer"
            
            for i in range(60):
                msg = ActorMessage.create(
                    sender_id="test",
                    message_type=MESSAGE_TYPES['STORE_MEMORY'],
                    payload={
                        'user_id': test_user,
                        'message_type': 'user' if i % 2 == 0 else 'bot',
                        'content': f'Message {i}',
                        'metadata': {'index': i}
                    }
                )
                await system.send_message("memory", msg)
            
            # Дать время на обработку всех сообщений
            await asyncio.sleep(1.0)
            
            # 3. Запросить контекст ЧЕРЕЗ АКТОРА
            get_msg = ActorMessage.create(
                sender_id="test",
                message_type=MESSAGE_TYPES['GET_CONTEXT'],
                payload={'user_id': test_user}
            )
            
            # Отправляем запрос напрямую актору для получения ответа
            response = await memory_actor.handle_message(get_msg)
            
            # 4. Проверить результат
            assert response is not None
            assert response.message_type == MESSAGE_TYPES['CONTEXT_RESPONSE']
            assert response.payload['total_messages'] == STM_BUFFER_SIZE
            
            # Проверить что это сообщения 10-59 (первые 10 удалены)
            messages = response.payload['messages']
            first_msg = messages[0]
            last_msg = messages[-1]
            
            # В structured формате поле 'content', в text формате тоже 'content'
            assert 'Message 10' in first_msg['content']
            assert 'Message 59' in last_msg['content']
            
            # Проверить чередование ролей
            for i, msg in enumerate(messages):
                expected_index = i + 10  # Начинаем с Message 10
                if 'role' in msg:  # structured format
                    expected_role = 'user' if expected_index % 2 == 0 else 'assistant'
                    assert msg['role'] == expected_role
                
        finally:
            await system.stop()
    
    async def test_message_truncation(self):
        """Тест: длинные сообщения обрезаются с пометкой"""
        system = ActorSystem("test-truncate")
        memory_actor = MemoryActor() 
        event_store = EventStore()
        
        system.set_event_store(event_store)
        await system.register_actor(memory_actor)
        await system.start()
        
        try:
            test_user = "test_user_truncate"
            
            # Создаем сообщение длиннее лимита
            long_content = "A" * (STM_MESSAGE_MAX_LENGTH + 100)
            
            msg = ActorMessage.create(
                sender_id="test",
                message_type=MESSAGE_TYPES['STORE_MEMORY'],
                payload={
                    'user_id': test_user,
                    'message_type': 'user',
                    'content': long_content
                }
            )
            await system.send_message("memory", msg)
            
            # Ждем обработки
            await asyncio.sleep(0.5)
            
            # Проверяем события в Event Store
            events = await event_store.get_stream(f"memory_{test_user}")
            assert len(events) > 0
            
            # Последнее событие должно быть MemoryStoredEvent
            last_event = events[-1]
            assert last_event.event_type == "MemoryStoredEvent"
            assert last_event.data['content_length'] == STM_MESSAGE_MAX_LENGTH
            assert last_event.data['has_metadata'] is True
            
            # Получаем контекст
            get_msg = ActorMessage.create(
                sender_id="test",
                message_type=MESSAGE_TYPES['GET_CONTEXT'],
                payload={'user_id': test_user}
            )
            
            response = await memory_actor.handle_message(get_msg)
            
            # Проверяем, что сообщение обрезано
            assert len(response.payload['messages']) == 1
            message_content = response.payload['messages'][0]['content']
            assert len(message_content) == STM_MESSAGE_MAX_LENGTH
            assert message_content == "A" * STM_MESSAGE_MAX_LENGTH
            
        finally:
            await system.stop()
    
    async def test_context_chronological_order(self):
        """Тест: контекст возвращается в хронологическом порядке"""
        system = ActorSystem("test-order")
        memory_actor = MemoryActor()
        event_store = EventStore()
        
        system.set_event_store(event_store)
        await system.register_actor(memory_actor)
        await system.start()
        
        try:
            test_user = "test_user_order"
            
            # Отправляем сообщения с временными метками
            for i in range(5):
                msg = ActorMessage.create(
                    sender_id="test",
                    message_type=MESSAGE_TYPES['STORE_MEMORY'],
                    payload={
                        'user_id': test_user,
                        'message_type': 'user',
                        'content': f'Message {i} at {datetime.now().isoformat()}'
                    }
                )
                await system.send_message("memory", msg)
                await asyncio.sleep(0.1)  # Небольшая задержка между сообщениями
            
            # Запрашиваем контекст
            get_msg = ActorMessage.create(
                sender_id="test",
                message_type=MESSAGE_TYPES['GET_CONTEXT'],
                payload={'user_id': test_user}
            )
            
            response = await memory_actor.handle_message(get_msg)
            
            # Проверяем порядок
            messages = response.payload['messages']
            assert len(messages) == 5
            
            # Сообщения должны идти от 0 до 4
            for i, msg in enumerate(messages):
                assert f'Message {i}' in msg['content']
            
        finally:
            await system.stop()
    
    async def test_format_types(self):
        """Тест: разные форматы вывода контекста"""
        system = ActorSystem("test-formats")
        memory_actor = MemoryActor()
        event_store = EventStore()
        
        system.set_event_store(event_store)
        await system.register_actor(memory_actor)
        await system.start()
        
        try:
            test_user = "test_user_formats"
            
            # Добавляем несколько сообщений
            for i in range(3):
                msg = ActorMessage.create(
                    sender_id="test",
                    message_type=MESSAGE_TYPES['STORE_MEMORY'],
                    payload={
                        'user_id': test_user,
                        'message_type': 'user' if i % 2 == 0 else 'bot',
                        'content': f'Test message {i}'
                    }
                )
                await system.send_message("memory", msg)
            
            await asyncio.sleep(0.5)
            
            # Тест structured формата (для DeepSeek)
            get_msg_structured = ActorMessage.create(
                sender_id="test",
                message_type=MESSAGE_TYPES['GET_CONTEXT'],
                payload={
                    'user_id': test_user,
                    'format_type': 'structured'
                }
            )
            
            response_structured = await memory_actor.handle_message(get_msg_structured)
            messages_structured = response_structured.payload['messages']
            
            # Проверяем формат для DeepSeek API
            assert messages_structured[0]['role'] == 'user'
            assert messages_structured[1]['role'] == 'assistant'
            assert messages_structured[2]['role'] == 'user'
            assert 'content' in messages_structured[0]
            
            # Тест text формата (для отладки)
            get_msg_text = ActorMessage.create(
                sender_id="test",
                message_type=MESSAGE_TYPES['GET_CONTEXT'],
                payload={
                    'user_id': test_user,
                    'format_type': 'text'
                }
            )
            
            response_text = await memory_actor.handle_message(get_msg_text)
            messages_text = response_text.payload['messages']
            
            # Проверяем текстовый формат
            assert messages_text[0]['type'] == 'user'
            assert messages_text[1]['type'] == 'bot'
            assert 'timestamp' in messages_text[0]
            
        finally:
            await system.stop()
    
    async def test_degraded_mode_behavior(self):
        """Тест: поведение в degraded mode"""
        # Создаем актора и вручную переводим в degraded mode
        memory_actor = MemoryActor()
        memory_actor._degraded_mode = True
        
        # Тест STORE_MEMORY в degraded mode
        store_msg = ActorMessage.create(
            sender_id="test",
            message_type=MESSAGE_TYPES['STORE_MEMORY'],
            payload={
                'user_id': 'test_user',
                'message_type': 'user',
                'content': 'Test message'
            }
        )
        
        # Не должно быть исключения
        await memory_actor.handle_message(store_msg)
        
        # Тест GET_CONTEXT в degraded mode
        get_msg = ActorMessage.create(
            sender_id="test",
            message_type=MESSAGE_TYPES['GET_CONTEXT'],
            payload={'user_id': 'test_user'}
        )
        
        response = await memory_actor.handle_message(get_msg)
        assert response is not None
        assert response.payload['degraded_mode'] is True
        assert response.payload['context'] == []


if __name__ == "__main__":
    # Запуск тестов
    pytest.main([__file__, "-v", "-s"])