"""Единственное место, где процесс читает окружение.

Всё, что ниже composition root, получает нужное аргументом
(см. BACKEND_ARCHITECTURE.md, раздел «Конфигурация»).
"""

from pydantic import Field, SecretStr, field_validator
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
    # OAuth того же GitHub App — вход пользователей. Пустые по той же причине:
    # без них не работает только вход, остальное API отвечает.
    github_app_client_id: str = Field(
        default="",
        description="Client ID GitHub App; из окружения GITHUB_APP_CLIENT_ID",
    )
    github_app_client_secret: SecretStr = Field(
        default=SecretStr(""),
        description="Client secret GitHub App; из окружения GITHUB_APP_CLIENT_SECRET",
    )
    # Публичный адрес сервиса без завершающего `/`: с ним сверяется `Origin`
    # изменяющих запросов, от него строится callback OAuth. Пустой не совпадёт
    # ни с одним `Origin` — изменяющие запросы с сессией отклоняются.
    public_origin: str = Field(
        default="",
        description="Публичный origin сервиса, например https://review.example; из PUBLIC_ORIGIN",
    )

    @field_validator("public_origin")
    @classmethod
    def _strip_trailing_slash(cls, value: str) -> str:
        return value.rstrip("/")


def load_settings() -> Settings:
    """Собрать настройки из окружения, громко упав, если какой-то не хватает."""
    return Settings()
