import os
from dotenv import load_dotenv

load_dotenv()


# ========================================
# ЛИМИТЫ ДЛЯ ДЕМО-ДОСТУПА
# ========================================

DAILY_MESSAGE_LIMIT = 10  # Количество сообщений в день для неавторизованных



# ========================================
# BACKEND-НАСТРОЙКИ
# ========================================

# Actor System настройки
ACTOR_SYSTEM_NAME = "chimera"
ACTOR_MESSAGE_QUEUE_SIZE = 1000     # Макс. размер очереди сообщений
ACTOR_SHUTDOWN_TIMEOUT = 5.0        # Секунды
ACTOR_MESSAGE_TIMEOUT = 1.0         # Таймаут ожидания сообщения в message loop

# Retry настройки
ACTOR_MESSAGE_RETRY_ENABLED = True  # Включить retry механизм
ACTOR_MESSAGE_MAX_RETRIES = 3       # Макс. количество попыток
ACTOR_MESSAGE_RETRY_DELAY = 0.1     # Начальная задержка между попытками (сек)
ACTOR_MESSAGE_RETRY_MAX_DELAY = 2.0 # Макс. задержка между попытками (сек)

# Circuit Breaker настройки
CIRCUIT_BREAKER_ENABLED = True          # Включить Circuit Breaker
CIRCUIT_BREAKER_FAILURE_THRESHOLD = 5   # Количество ошибок для открытия
CIRCUIT_BREAKER_RECOVERY_TIMEOUT = 60   # Время восстановления в секундах

# Логирование
LOG_LEVEL = "INFO"
LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# JSON логирование
ENABLE_JSON_LOGGING = True  # Включить JSON логирование параллельно с текстовым
JSON_LOG_FILE = "logs/chimera.json"  # Путь к файлу JSON логов

# Ротация логов
LOG_ROTATION_ENABLED = True  # Включить ротацию файлов логов
LOG_MAX_BYTES = 1 * 1024 * 1024  # Макс. размер файла логов (1 МБ)
LOG_BACKUP_COUNT = 5  # Количество архивных файлов логов

# Мониторинг
ENABLE_PERFORMANCE_METRICS = True
METRICS_LOG_INTERVAL = 60  # Секунды
SLOW_OPERATION_THRESHOLD = 0.1  # Порог для медленных операций (секунды)

# Dead Letter Queue настройки
DLQ_MAX_SIZE = 1000  # Макс. размер очереди перед автоочисткой
DLQ_CLEANUP_INTERVAL = 3600  # Интервал очистки в секундах (1 час)
DLQ_METRICS_ENABLED = True  # Включить метрики DLQ

# Event Store настройки
EVENT_STORE_TYPE = "memory"              # Тип хранилища (будет "postgres" в фазе 3)
EVENT_STORE_MAX_MEMORY_EVENTS = 10000    # Макс. событий в памяти
EVENT_STORE_STREAM_CACHE_SIZE = 100      # Размер LRU кэша потоков
EVENT_STORE_CLEANUP_INTERVAL = 3600      # Интервал очистки старых событий (сек)
EVENT_STORE_CLEANUP_BATCH_SIZE = 100     # Размер батча при очистке

# Сериализация событий
EVENT_SERIALIZATION_FORMAT = "json"
EVENT_TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"



# ========================================
# POSTGRESQL EVENT STORE
# ========================================

# PostgreSQL подключение
POSTGRES_DSN = os.getenv("POSTGRES_DSN", 
    "postgresql://chimera_user:password@localhost:5432/chimera_db")
POSTGRES_POOL_MIN_SIZE = 10        # Минимальный размер пула подключений
POSTGRES_POOL_MAX_SIZE = 20        # Максимальный размер пула подключений
POSTGRES_COMMAND_TIMEOUT = 60      # Таймаут команд в секундах
POSTGRES_CONNECT_TIMEOUT = 10      # Таймаут подключения в секундах
POSTGRES_RETRY_ATTEMPTS = 3        # Количество попыток переподключения
POSTGRES_RETRY_DELAY = 1.0         # Задержка между попытками в секундах

# Батчевая запись событий
EVENT_STORE_BATCH_SIZE = 100       # Размер батча для записи
EVENT_STORE_FLUSH_INTERVAL = 1.0   # Интервал автоматического flush в секундах
EVENT_STORE_MAX_BUFFER_SIZE = 1000 # Максимальный размер буфера записи

# Миграция данных
EVENT_STORE_MIGRATION_BATCH = 1000 # Размер батча при миграции
EVENT_STORE_MIGRATION_DELAY = 0.1  # Задержка между батчами миграции (сек)
EVENT_STORE_MIGRATION_VERIFY = True # Верифицировать данные после миграции

# Переключение реализации
EVENT_STORE_TYPE = "postgres"         # "memory" или "postgres" (пока оставляем memory)

# Advisory lock настройки
USE_DOUBLE_KEY_ADVISORY_LOCK = True  # Использовать два ключа для уменьшения коллизий



# ========================================
# DEEPSEEK & TELEGRAM
# ========================================

# DeepSeek API настройки
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"
DEEPSEEK_MODEL = "deepseek-chat"
DEEPSEEK_TIMEOUT = 30  # Сек
DEEPSEEK_MAX_RETRIES = 3

# Telegram Bot настройки
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_POLLING_TIMEOUT = 30
TELEGRAM_TYPING_UPDATE_INTERVAL = 5
TELEGRAM_MAX_MESSAGE_LENGTH = 4096
TELEGRAM_TYPING_CLEANUP_THRESHOLD = 100  # Порог для очистки завершенных typing задач
TELEGRAM_API_DEFAULT_TIMEOUT = 10        # Таймаут по умолчанию для API вызовов
TELEGRAM_MAX_TYPING_TASKS = 1000         # Макс. количество одновременных typing задач

# Метрики и адаптивная стратегия
CACHE_HIT_LOG_INTERVAL = 10
MIN_CACHE_HIT_RATE = 0.5



# ========================================
# JSON-ОТВЕТЫ
# ========================================

# Параметры валидации JSON-ответов
JSON_VALIDATION_ENABLED = True  # Включить валидацию структурированных ответов
JSON_VALIDATION_LOG_FAILURES = True  # Логировать неудачные валидации
JSON_VALIDATION_EVENT_BATCH_SIZE = 10  # Размер батча для событий валидации



# ========================================
# РЕЖИМЫ
# ========================================

# Настройки истории режимов
MODE_HISTORY_SIZE = 5  # Макс. размер истории режимов
MODE_CONFIDENCE_THRESHOLD = 0.3  # Мин. уверенность для режима по умолчанию
MODE_SCORE_NORMALIZATION_FACTOR = 1.5  # Делитель для нормализации уверенности

# Веса для контекстных паттернов
CONTEXTUAL_PATTERN_PHRASE_WEIGHT = 2.5  # Вес для точных фраз
CONTEXTUAL_PATTERN_DOMAIN_WEIGHT = 0.5  # Вес для доменных маркеров
CONTEXTUAL_PATTERN_CONTEXT_MULTIPLIER = 1.5  # Множитель для контекстных слов
CONTEXTUAL_PATTERN_SUPPRESSOR_MULTIPLIER = 0.0  # Множитель для подавителей

# Производительность определения режимов
MODE_DETECTION_CACHE_ENABLED = True  # Кэшировать результаты паттернов
MODE_DETECTION_MAX_TIME_MS = 5  # Макс. время определения в миллисекундах
MODE_DETECTION_DEBUG_LOGGING = True  # Логировать детали определения



# ========================================
# PYDANTIC
# ========================================

# Параметры валидации Pydantic моделей
PYDANTIC_RESPONSE_MIN_LENGTH = 1  # Минимальная длина поля response
PYDANTIC_CONFIDENCE_MIN = 0.0  # Мин. значение confidence/engagement_level
PYDANTIC_CONFIDENCE_MAX = 1.0  # Макс. значение confidence/engagement_level
PYDANTIC_STRING_LIST_COERCE = True  # Преобразовывать элементы списков в строки
PYDANTIC_VALIDATION_STRICT = False  # Строгий режим валидации (без приведения типов)

# Параметры валидации основных структур данных
PYDANTIC_MESSAGE_TYPE_MIN_LENGTH = 0  # Мин. длина message_type (0 = может быть пустым)
PYDANTIC_EVENT_TYPE_MIN_LENGTH = 1    # Мин. длина event_type (минимум 1 символ)
PYDANTIC_STREAM_ID_MIN_LENGTH = 0     # Мин. длина stream_id (0 = может быть пустым)
PYDANTIC_MODE_HISTORY_MAX_SIZE = 10   # Макс. размер истории режимов в UserSession
PYDANTIC_CACHE_METRICS_MAX_SIZE = 100 # Макс. размер метрик кэша в UserSession



# ========================================
# SHORT-TERM MEMORY (STM)
# ========================================

# Short-Term Memory (STM)

# STM Buffer settings
STM_BUFFER_SIZE = 50                    # Number of messages to keep per user
STM_CLEANUP_BATCH_SIZE = 10             # Number of records to delete at once
STM_QUERY_TIMEOUT = 5.0                 # Query timeout in seconds
STM_CONTEXT_FORMAT = "structured"       # Format: structured (for DeepSeek), text (for debug)
STM_INCLUDE_METADATA = True             # Include metadata in context
STM_MESSAGE_MAX_LENGTH = 4000           # Maximum length of a single message before truncation
STM_CONTEXT_SIZE_FOR_GENERATION = 20   # Number of historical messages to include in generation context

# Role mapping for DeepSeek API
STM_DEEPSEEK_ROLE_MAPPING = {
    "user": "user",
    "bot": "assistant"
}

# STM Metrics
STM_METRICS_ENABLED = True              # Enable metrics collection
STM_METRICS_LOG_INTERVAL = 300          # Metrics logging interval in seconds

# Context request handling
STM_CONTEXT_REQUEST_TIMEOUT = 30        # Timeout for pending context requests in seconds