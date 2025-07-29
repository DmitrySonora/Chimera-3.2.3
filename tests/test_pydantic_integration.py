import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock
from datetime import datetime

from actors.actor_system import ActorSystem
from actors.events import EventStore
from actors.user_session_actor import UserSessionActor
from actors.generation_actor import GenerationActor
from actors.messages import ActorMessage, MESSAGE_TYPES
from config.prompts import MODE_GENERATION_PARAMS


class TestPydanticIntegration:
    """Интеграционные тесты для проверки Pydantic и режимных параметров"""
    
    @pytest.mark.asyncio
    async def test_full_cycle_with_modes(self):
        """Тест полного цикла работы системы с разными режимами"""
        # Создаем систему
        system = ActorSystem("test")
        event_store = EventStore()
        system.set_event_store(event_store)
        
        # Создаем акторы
        session_actor = UserSessionActor()
        generation_actor = GenerationActor()
        
        # Мокаем DeepSeek API
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.__aiter__.return_value = [
            MagicMock(choices=[MagicMock(delta=MagicMock(content='{"response": "Тестовый ответ"}'))])
        ]
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
        generation_actor._client = mock_client
        
        # Регистрируем акторы
        await system.register_actor(session_actor)
        await system.register_actor(generation_actor)
        
        # Запускаем систему
        await system.start()
        
        # Тестовые сообщения для разных режимов
        test_cases = [
            ("Привет, как дела?", "talk"),
            ("Объясни, как работает нейросеть", "expert"),
            ("Придумай историю про дракона", "creative")
        ]
        
        for text, expected_mode in test_cases:
            # Отправляем сообщение
            msg = ActorMessage.create(
                sender_id="test",
                message_type=MESSAGE_TYPES['USER_MESSAGE'],
                payload={
                    'user_id': f'test_user_{expected_mode}',
                    'chat_id': 123,
                    'username': 'test',
                    'text': text,
                    'timestamp': datetime.now().isoformat()
                }
            )
            
            await system.send_message("user_session", msg)
            await asyncio.sleep(0.1)  # Даем время на обработку
            
            # Проверяем, что режим определился правильно
            session = session_actor._sessions.get(f'test_user_{expected_mode}')
            assert session is not None
            assert session.current_mode == expected_mode
            
            # Проверяем события
            events = await event_store.get_stream(f"user_{f'test_user_{expected_mode}'}")
            assert len(events) > 0
            
            # Проверяем, что использовались правильные параметры
            api_call_args = mock_client.chat.completions.create.call_args
            if api_call_args:
                kwargs = api_call_args[1]
                mode_params = MODE_GENERATION_PARAMS.get(expected_mode, MODE_GENERATION_PARAMS["base"])
                assert kwargs['temperature'] == mode_params['temperature']
                assert kwargs['max_tokens'] == mode_params['max_tokens']
        
        await system.stop()
        print("✅ Полный цикл работы с режимами протестирован")
    
    @pytest.mark.asyncio
    async def test_pydantic_validation_in_generation(self):
        """Тест Pydantic валидации в GenerationActor"""
        actor = GenerationActor()
        
        # Инициализируем минимальные зависимости
        from config.logging import get_logger
        actor.logger = get_logger("test")
        from utils.event_utils import EventVersionManager
        actor._event_version_manager = EventVersionManager()
        
        # Тест успешной валидации
        valid_data = {
            "response": "Тестовый ответ",
            "emotional_tone": "дружелюбный",
            "engagement_level": 0.8
        }
        is_valid, errors = await actor._validate_structured_response(valid_data, mode="talk")
        assert is_valid is True
        assert len(errors) == 0
        print("✅ Валидация корректных данных успешна")
        
        # Тест обнаружения ошибок
        invalid_data = {
            "response": "",  # Пустой ответ
            "confidence": "высокая"  # Должно быть число
        }
        is_valid, errors = await actor._validate_structured_response(invalid_data, mode="expert")
        assert is_valid is False
        assert len(errors) > 0
        assert any("response" in error for error in errors)
        print(f"✅ Обнаружены ошибки валидации: {errors[:2]}")
        
        # Тест валидации creative режима
        creative_data = {
            "response": "Дракон спал на облаке",
            "style_markers": ["метафора", "персонификация"],
            "metaphors": ["облако из снов"]
        }
        is_valid, errors = await actor._validate_structured_response(creative_data, mode="creative")
        assert is_valid is True
        print("✅ Валидация creative режима успешна")
    
    @pytest.mark.asyncio
    async def test_mode_parameters_application(self):
        """Тест применения режимных параметров"""
        print("\n📊 Параметры генерации для разных режимов:\n")
        
        for mode, params in MODE_GENERATION_PARAMS.items():
            print(f"Режим '{mode}':")
            print(f"  • temperature: {params.get('temperature', 'default')}")
            print(f"  • top_p: {params.get('top_p', 'default')}")
            print(f"  • max_tokens: {params.get('max_tokens', 'default')}")
            print(f"  • frequency_penalty: {params.get('frequency_penalty', 'default')}")
            print(f"  • presence_penalty: {params.get('presence_penalty', 'default')}")
            print()
        
        # Проверяем различия
        talk_temp = MODE_GENERATION_PARAMS["talk"]["temperature"]
        expert_temp = MODE_GENERATION_PARAMS["expert"]["temperature"]
        creative_temp = MODE_GENERATION_PARAMS["creative"]["temperature"]
        
        assert talk_temp != expert_temp, "Параметры talk и expert должны различаться"
        assert expert_temp != creative_temp, "Параметры expert и creative должны различаться"
        assert expert_temp < talk_temp, "Expert должен быть менее креативным чем talk"
        
        print("✅ Параметры для режимов корректно различаются")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])