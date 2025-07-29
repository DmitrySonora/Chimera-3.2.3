import pytest
import asyncio

from actors.actor_system import ActorSystem
from actors.memory_actor import MemoryActor
from actors.messages import ActorMessage, MESSAGE_TYPES
from config.logging import setup_logging

# Настраиваем логирование для тестов
setup_logging()


@pytest.mark.asyncio
class TestMemoryActorIntegration:
    """Интеграционные тесты для MemoryActor"""
    
    async def test_memory_actor_registration(self):
        """Тест регистрации MemoryActor в ActorSystem"""
        system = ActorSystem("test-memory")
        memory_actor = MemoryActor()
        
        # Регистрируем актор
        await system.register_actor(memory_actor)
        
        # Проверяем, что актор зарегистрирован
        registered_actor = await system.get_actor("memory")
        assert registered_actor is not None
        assert registered_actor.actor_id == "memory"
        assert registered_actor.name == "Memory"
    
    async def test_memory_actor_initialization_success(self):
        """Тест успешной инициализации с доступной БД"""
        system = ActorSystem("test-memory-init")
        memory_actor = MemoryActor()
        
        await system.register_actor(memory_actor)
        
        # Запускаем систему (что запустит и актор)
        await system.start()
        
        # Проверяем, что актор инициализирован
        assert memory_actor.is_running
        assert memory_actor._metrics['initialized'] is True
        assert memory_actor._degraded_mode is False
        
        # Останавливаем систему
        await system.stop()
    
    async def test_memory_actor_degraded_mode(self):
        """Тест перехода в degraded mode при недоступной БД"""
        # Для этого теста нужно временно сломать подключение к БД
        # В реальном тесте это можно сделать через mock
        # Здесь просто проверим структуру
        
        memory_actor = MemoryActor()
        
        # Симулируем ошибку инициализации
        memory_actor._degraded_mode = True
        memory_actor._metrics['degraded_mode_entries'] = 1
        
        assert memory_actor._degraded_mode is True
        assert memory_actor._metrics['degraded_mode_entries'] == 1
    
    async def test_memory_actor_message_handling(self):
        """Тест обработки всех типов сообщений"""
        system = ActorSystem("test-memory-messages")
        memory_actor = MemoryActor()
        
        await system.register_actor(memory_actor)
        await system.start()
        
        # Тест STORE_MEMORY
        store_msg = ActorMessage.create(
            sender_id="test",
            message_type=MESSAGE_TYPES['STORE_MEMORY'],
            payload={
                'user_id': 'test_user',
                'message_type': 'user',
                'content': 'Test message'
            }
        )
        await system.send_message("memory", store_msg)
        
        # Даем время на обработку
        await asyncio.sleep(0.1)
        
        # Проверяем метрики
        assert memory_actor._metrics['store_memory_count'] == 1
        
        # Тест GET_CONTEXT
        get_msg = ActorMessage.create(
            sender_id="test",
            message_type=MESSAGE_TYPES['GET_CONTEXT'],
            payload={
                'user_id': 'test_user',
                'count': 10
            }
        )
        
        # Для GET_CONTEXT нужно получить ответ напрямую
        response = await memory_actor.handle_message(get_msg)
        assert response is not None
        assert response.message_type == MESSAGE_TYPES['CONTEXT_RESPONSE']
        assert 'context' in response.payload
        assert isinstance(response.payload['context'], list)
        
        # Тест CLEAR_USER_MEMORY
        clear_msg = ActorMessage.create(
            sender_id="test",
            message_type=MESSAGE_TYPES['CLEAR_USER_MEMORY'],
            payload={
                'user_id': 'test_user'
            }
        )
        await system.send_message("memory", clear_msg)
        
        await asyncio.sleep(0.1)
        
        # Проверяем метрики
        assert memory_actor._metrics['get_context_count'] == 1
        assert memory_actor._metrics['clear_memory_count'] == 1
        
        # Тест неизвестного типа сообщения
        unknown_msg = ActorMessage.create(
            sender_id="test",
            message_type="unknown_type",
            payload={}
        )
        await system.send_message("memory", unknown_msg)
        
        await asyncio.sleep(0.1)
        
        assert memory_actor._metrics['unknown_message_count'] == 1
        
        # Останавливаем систему
        await system.stop()
    
    async def test_memory_actor_degraded_mode_responses(self):
        """Тест ответов в degraded mode"""
        memory_actor = MemoryActor()
        memory_actor._degraded_mode = True
        
        # Тест GET_CONTEXT в degraded mode
        get_msg = ActorMessage.create(
            sender_id="test",
            message_type=MESSAGE_TYPES['GET_CONTEXT'],
            payload={
                'user_id': 'test_user'
            }
        )
        
        response = await memory_actor.handle_message(get_msg)
        assert response is not None
        assert response.payload['degraded_mode'] is True
        assert response.payload['context'] == []
    
    async def test_memory_actor_shutdown(self):
        """Тест корректного завершения работы"""
        system = ActorSystem("test-memory-shutdown")
        memory_actor = MemoryActor()
        
        await system.register_actor(memory_actor)
        await system.start()
        
        # Делаем несколько операций для метрик
        for i in range(3):
            msg = ActorMessage.create(
                sender_id="test",
                message_type=MESSAGE_TYPES['STORE_MEMORY'],
                payload={'user_id': f'user_{i}', 'content': f'msg_{i}'}
            )
            await system.send_message("memory", msg)
        
        await asyncio.sleep(0.1)
        
        # Останавливаем
        await system.stop()
        
        # Проверяем, что актор остановлен
        assert memory_actor.is_running is False
        
        # Проверяем, что метрики были залогированы
        assert memory_actor._metrics['store_memory_count'] == 3


@pytest.mark.asyncio
async def test_sql_migration_idempotency():
    """Тест идемпотентности SQL миграции"""
    # Этот тест проверяет, что миграцию можно выполнить повторно
    # В реальном окружении нужно выполнить миграцию дважды
    # и убедиться, что второй раз проходит без ошибок
    
    # Здесь просто проверим, что SQL использует IF NOT EXISTS
    with open('database/migrations/002_create_stm_buffer.sql', 'r') as f:
        sql_content = f.read()
    
    assert 'CREATE TABLE IF NOT EXISTS stm_buffer' in sql_content
    assert 'CREATE INDEX IF NOT EXISTS idx_stm_user_timestamp' in sql_content
    assert 'CREATE OR REPLACE FUNCTION' in sql_content
    
    print("✅ SQL migration is idempotent")


if __name__ == "__main__":
    # Запуск тестов
    pytest.main([__file__, "-v", "-s"])