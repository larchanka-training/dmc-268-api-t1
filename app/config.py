"""Единственное место, где процесс читает окружение.

Всё, что ниже composition root, получает нужное аргументом
(см. BACKEND_ARCHITECTURE.md, раздел «Конфигурация»).
"""

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Конфигурация приложения, собираемая один раз на старте.

    Только то, что сегодня кто-то действительно читает. Настройки авторизации,
    rate limit и кэша появятся вместе с кодом, который их использует, не
    раньше.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = Field(
        description="PostgreSQL DSN, e.g. postgresql+psycopg://user:pass@host/db",
    )
    rabbitmq_url: str = Field(
        description="RabbitMQ AMQP DSN, e.g. amqp://user:pass@host//",
    )
    # Секрет проверки HMAC-подписи вебхуков GitHub (X-Hub-Signature-256).
    # Обязателен: пустой секрет — HMAC с ключом, который знает каждый, это
    # то же самое, что отсутствие проверки подписи. SecretStr, а не str:
    # Settings попадает в dataclass с рабочим __repr__, и обычная строка
    # ушла бы в трейсбек с локальными переменными целиком.
    github_webhook_secret: SecretStr = Field(
        min_length=1,
        description="Shared secret GitHub signs webhook deliveries with (X-Hub-Signature-256).",
    )
    # Дольше самой долгой ожидаемой обработки: иначе выметатель зависших
    # объявит живой прогон мёртвым (docs/SYSTEM_DESIGN.md, `app/worker`).
    stale_run_timeout_seconds: int = Field(
        default=1800,
        gt=0,
        description="How long a non-terminal review run may go without progress before the worker fails it.",
    )
    # Пустые по умолчанию, а не обязательные: composition root строит
    # VCS-шлюз лениво, и приложение без GitHub-реквизитов продолжает отвечать
    # на запросы, не касающиеся вебхуков. Пустой ключ громко аукнется при
    # первом же обращении к VCS API.
    github_app_id: str = Field(
        default="",
        description="Идентификатор GitHub App; из окружения GITHUB_APP_ID",
    )
    github_app_private_key: SecretStr = Field(
        default=SecretStr(""),
        description="PEM private key GitHub App; из окружения GITHUB_APP_PRIVATE_KEY",
    )
    # Лимиты сборки контекста прогона (change add-context-engine-and-repo-rules).
    # Жёсткий потолок пакета, который видит модель: оценка токенов тела
    # никогда его не превышает, не поместившееся перечисляется в `omitted`.
    # Нижняя граница — чтобы в лимит гарантированно влезал каркас пакета.
    context_max_tokens: int = Field(
        default=32_000,
        ge=1_024,
        description="Жёсткий лимит оценки токенов пакета контекста прогона",
    )
    rules_max_tokens: int = Field(
        default=4_000,
        gt=0,
        description="Доля бюджета контекста под правила владельца репозитория",
    )
    rules_max_bytes: int = Field(
        default=32_768,
        gt=0,
        description="Больший .review/rules.md не разбирается — дефолтный фоллбэк",
    )
    rules_cache_ttl_seconds: int = Field(
        default=3_600,
        gt=0,
        description="Сколько живёт в CacheStore результат загрузки правил",
    )
    ast_max_files: int = Field(
        default=20,
        gt=0,
        description="Сколько файлов диффа довыбирается для AST-окружения",
    )
    ast_max_file_bytes: int = Field(
        default=262_144,
        gt=0,
        description="Больший файл попадает в контекст без AST-окружения",
    )


def load_settings() -> Settings:
    """Собрать настройки из окружения, громко упав, если какой-то не хватает."""
    return Settings()
