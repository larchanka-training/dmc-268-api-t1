"""Единственное место, где процесс читает окружение.

Всё, что ниже composition root, получает нужное аргументом
(см. BACKEND_ARCHITECTURE.md, раздел «Конфигурация»).
"""

from typing import Literal, Self

from pydantic import Field, SecretStr, model_validator
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
    # Потолок пакета контекста — только данных, без системного промпта и
    # ответа модели: окно модели должно вмещать пакет + промпты + ответ.
    # Соблюдается оценка (байты / 4), а не точное число токенов — см.
    # `app.domain.context_budget`. Не поместившееся перечисляется в `omitted`.
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
    # Доля пакета под контекст PR - заголовок, описание, сообщения коммитов;
    # берётся первой, неистраченное уходит диффу (change add-llm-gateway, D6).
    pr_context_max_tokens: int = Field(
        default=1_000,
        gt=0,
        description="Доля бюджета контекста под заголовок, описание и коммиты PR",
    )
    # Окно модели и резерв в нём под ответ (change add-llm-gateway, design D5).
    # Окно одно на все провайдеры - наименьшее из окон настроенных моделей;
    # резерв уходит провайдеру пределом длины ответа.
    llm_context_window_tokens: int = Field(
        default=65_536,
        gt=0,
        description="Окно модели в токенах: запрос вместе с резервом ответа обязан в него помещаться",
    )
    llm_max_output_tokens: int = Field(
        default=4_096,
        gt=0,
        description="Резерв окна под ответ модели и предел длины ответа",
    )
    # Провайдеры модели (change add-llm-gateway, design D3 и D7). Не задан -
    # воркер не стартует: заглушка выбирается только явно. Необязательна здесь,
    # потому что настройки общие с API, которому модель не нужна.
    llm_backend: Literal["stub", "ollama"] | None = Field(
        default=None,
        description="Адаптер модели воркера: stub или ollama; обязателен для воркера",
    )
    ollama_base_url: str = Field(
        default="http://ollama:11434",
        description="Адрес Ollama в нашей сети",
    )
    ollama_model: str = Field(
        default="",
        description="Модель Ollama; обязательна при LLM_BACKEND=ollama",
    )
    # Код проверяемого репозитория уходит hosted-провайдеру только при явном
    # включении и только после сбоя локальной модели: основным он не бывает.
    llm_hosted_fallback: bool = Field(
        default=False,
        description="Включить Eurorouter фоллбэком после локальной модели",
    )
    eurorouter_base_url: str = Field(
        default="",
        description="Адрес OpenAI-совместимого API Eurorouter, например https://…/v1",
    )
    eurorouter_model: str = Field(default="", description="Модель Eurorouter")
    # Несколько ключей через запятую: ключ, упёршийся в лимит, сменяется
    # следующим (design D4). SecretStr - по той же причине, что ключ App.
    eurorouter_api_keys: SecretStr = Field(
        default=SecretStr(""),
        description="Ключи Eurorouter через запятую",
    )
    llm_max_attempts: int = Field(
        default=2,
        ge=1,
        description="Попыток получить ответ по схеме у одного провайдера",
    )
    llm_timeout_seconds: float = Field(
        default=300,
        gt=0,
        description="Таймаут одного HTTP-запроса к модели, секунды",
    )

    @model_validator(mode="after")
    def _check_model_providers(self) -> Self:
        if self.llm_backend == "ollama" and not self.ollama_model:
            raise ValueError("OLLAMA_MODEL обязателен при LLM_BACKEND=ollama")
        if not self.llm_hosted_fallback:
            return self
        if self.llm_backend != "ollama":
            raise ValueError("LLM_HOSTED_FALLBACK требует LLM_BACKEND=ollama: hosted - только фоллбэк")
        missing = [
            name
            for name, value in (
                ("EUROROUTER_BASE_URL", self.eurorouter_base_url),
                ("EUROROUTER_MODEL", self.eurorouter_model),
                ("EUROROUTER_API_KEYS", self.eurorouter_keys),
            )
            if not value
        ]
        if missing:
            raise ValueError(f"LLM_HOSTED_FALLBACK=true требует {', '.join(missing)}")
        return self

    @property
    def eurorouter_keys(self) -> tuple[SecretStr, ...]:
        """Ключи Eurorouter по одному, без пустых и пробелов вокруг."""
        raw = self.eurorouter_api_keys.get_secret_value()
        return tuple(SecretStr(key.strip()) for key in raw.split(",") if key.strip())


def load_settings() -> Settings:
    """Собрать настройки из окружения, громко упав, если какой-то не хватает."""
    return Settings()
