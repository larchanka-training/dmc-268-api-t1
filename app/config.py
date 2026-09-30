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
    # Пустые по умолчанию, а не обязательные: composition root строит
    # VCS-шлюз лениво, и приложение без GitHub-реквизитов продолжает отвечать
    # на запросы, не касающиеся вебхуков. Пустой ключ громко аукнется при
    # первом же обращении к VCS API. Ключ — SecretStr, а не str: Settings
    # попадает в dataclass с рабочим __repr__, и обычная строка ушла бы в
    # трейсбек с локальными переменными целиком.
    github_app_id: str = Field(
        default="",
        description="Идентификатор GitHub App; из окружения GITHUB_APP_ID",
    )
    github_app_private_key: SecretStr = Field(
        default=SecretStr(""),
        description="PEM private key GitHub App; из окружения GITHUB_APP_PRIVATE_KEY",
    )
    # Секрет проверки HMAC-подписи вебхуков. Пустой по умолчанию по той же
    # причине, что и реквизиты выше; пустой секрет означает, что эндпоинт
    # отклоняет все вебхуки (401), а не принимает неподписанные.
    github_webhook_secret: SecretStr = Field(
        default=SecretStr(""),
        description="Секрет HMAC вебхуков GitHub; из окружения GITHUB_WEBHOOK_SECRET",
    )
    rabbitmq_url: str = Field(
        default="",
        description="Строка подключения AMQP; из окружения RABBITMQ_URL",
    )


def load_settings() -> Settings:
    """Собрать настройки из окружения, громко упав, если какой-то не хватает."""
    return Settings()
