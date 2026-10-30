"""Единственное место, где процесс читает окружение.

Всё, что ниже composition root, получает нужное аргументом
(см. BACKEND_ARCHITECTURE.md, раздел «Конфигурация»).
"""

import base64
import binascii
from typing import Literal, Self

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Длина ключа AES-256-GCM шифрования токенов пользователя.
USER_TOKEN_KEY_BYTES = 32


class Settings(BaseSettings):
    """Конфигурация приложения, собираемая один раз на старте.

    Только то, что сегодня кто-то действительно читает. Настройки авторизации,
    rate limit и кэша появятся вместе с кодом, который их использует, не
    раньше.
    """

    # hide_input_in_errors: ошибка проверки настройки не печатает её значение —
    # битый ключ или секрет не попадает в лог упавшего старта.
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", hide_input_in_errors=True)

    database_url: str = Field(
        description="PostgreSQL DSN, e.g. postgresql+psycopg://user:pass@host/db",
    )
    # Размер пула соединений SQLAlchemy (QueuePool) у API и воркера. Задан
    # явно, а не умолчанием диалекта: `instrument_pool` публикует предел пула
    # (`db.client.connection.max`) как pool_size + max_overflow, и метрика
    # обязана совпадать с фактической конфигурацией движка.
    db_pool_size: int = Field(
        default=5,
        gt=0,
        description="Размер пула соединений с базой у API и воркера",
    )
    db_max_overflow: int = Field(
        default=10,
        ge=0,
        description="Допустимые сверх пула соединения (overflow) у API и воркера",
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
    # Прогресс `queued` — время создания: порог больше любого разумного хвоста
    # очереди, иначе ждущий прогон выметается и ревью теряется молча. Больше
    # stale_run_timeout_seconds — проверяет воркер при старте (change queued-run-timeout).
    queued_run_timeout_seconds: int = Field(
        default=21_600,
        gt=0,
        description="Сколько прогон может ждать в queued, прежде чем воркер переведёт его в failed",
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

    # Ключ шифрования токенов пользователя GitHub в базе (AES-256-GCM):
    # 32 байта в base64, `openssl rand -base64 32`. Пустой — вход отказывает
    # (токен открытым не пишется никогда), битый — старт падает здесь же.
    user_token_encryption_key: SecretStr = Field(
        default=SecretStr(""),
        description="Ключ шифрования токенов пользователя, base64 32 байт; из USER_TOKEN_ENCRYPTION_KEY",
    )

    @field_validator("user_token_encryption_key")
    @classmethod
    def _check_token_key(cls, value: SecretStr) -> SecretStr:
        raw = value.get_secret_value()
        if raw.strip():
            # Только проверка формата; сообщение — без значения ключа.
            _decode_token_key(raw)
        return value

    @property
    def user_token_key(self) -> bytes | None:
        """Ключ шифрования токенов пользователя; `None` — не задан."""
        raw = self.user_token_encryption_key.get_secret_value()
        return _decode_token_key(raw) if raw.strip() else None

    @field_validator("public_origin")
    @classmethod
    def _strip_trailing_slash(cls, value: str) -> str:
        return value.rstrip("/")
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
    visible_repositories_cache_ttl_seconds: int = Field(
        default=30,
        gt=0,
        description=(
            "Сколько живёт в CacheStore список репозиториев, видимых пользователю на GitHub; "
            "отзыв доступа на GitHub виден с этой задержкой"
        ),
    )
    # Потолки тела HTTP-запроса API (#72): проверяются до разбора тела, сессии и подписи
    # вебхука. Ручкам с JSON-телом хватает 1 МиБ. Приёму вебхука — 2 МиБ: принимаются только
    # `pull_request` и `pull_request_review_thread`, их доставка — десятки КиБ (тело PR и
    # комментария — до 65 536 символов). Потолок доставки GitHub (25 MB) нужен `push` и
    # `release`, а 25 МиБ без подписи — OOM API с дюжины параллельных запросов.
    # `client_max_body_size` в `location /api/` nginx фронта (dmc-268-ui-t1, `nginx/site.conf`)
    # — первый эшелон — не меньше `webhook_body_max_bytes`.
    request_body_max_bytes: int = Field(
        default=1_048_576,
        gt=0,
        description="Большее тело запроса API, кроме вебхука, отклоняется 413 REQUEST_BODY_TOO_LARGE",
    )
    webhook_body_max_bytes: int = Field(
        default=2_097_152,
        gt=0,
        description="Большее тело доставки вебхука отклоняется 413 REQUEST_BODY_TOO_LARGE до чтения целиком",
    )
    # Потолок диффа прогона: тело читается потоком и обрывается на лимите,
    # больший дифф не ревьюится — прогон `failed` с `DIFF_TOO_LARGE` (#144).
    diff_max_bytes: int = Field(
        default=5_242_880,
        gt=0,
        description="Больший unified diff PR не дочитывается и не ревьюится",
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
    # eurorouter - hosted-провайдер основным и единственным: явный выбор отправлять
    # код проверяемых репозиториев наружу (change eurorouter-primary-backend).
    llm_backend: Literal["stub", "ollama", "eurorouter"] | None = Field(
        default=None,
        description="Адаптер модели воркера: stub, ollama или eurorouter; обязателен для воркера",
    )
    ollama_base_url: str = Field(
        default="http://ollama:11434",
        description="Адрес Ollama в нашей сети",
    )
    ollama_model: str = Field(
        default="",
        description="Модель Ollama; обязательна при LLM_BACKEND=ollama",
    )
    # Код проверяемого репозитория уходит hosted-провайдеру только по явному
    # выбору: этим флагом - после сбоя локальной модели, или LLM_BACKEND=eurorouter.
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

    # Повтор упавших доставок вебхука (#149): GitHub сам их не повторяет. Без
    # GITHUB_APP_ID и ключа повтор выключен, даже если флаг включён.
    webhook_redelivery_enabled: bool = Field(
        default=True,
        description="Переотправлять ли упавшие доставки вебхука GitHub App из воркера",
    )
    webhook_redelivery_interval_seconds: int = Field(
        default=300,
        gt=0,
        description="Как часто воркер проверяет упавшие доставки вебхука, секунды",
    )
    # 5 попыток с паузой 5, 10, 20, 40 и 80 минут переживают простой около 2,5 часов.
    webhook_redelivery_max_attempts: int = Field(
        default=5,
        gt=0,
        description="Сколько раз переотправлять одну доставку вебхука",
    )
    webhook_redelivery_backoff_seconds: int = Field(
        default=300,
        gt=0,
        description="Пауза перед первой переотправкой, секунды; дальше вдвое больше с каждой",
    )
    webhook_redelivery_max_per_pass: int = Field(
        default=10,
        gt=0,
        description="Сколько доставок вебхука переотправлять за один проход воркера",
    )
    # Проход идёт внутри consume-цикла воркера: на кандидата до трёх запросов к
    # GitHub (payload доставки, голова PR, переотправка), бюджет держит цикл живым.
    webhook_redelivery_pass_budget_seconds: float = Field(
        default=30,
        gt=0,
        description="Сколько секунд может идти один проход повтора доставок вебхука; остальное — в следующий",
    )

    @field_validator("llm_backend", mode="before")
    @classmethod
    def _empty_backend_is_unset(cls, value: object) -> object:
        # docker compose передаёт незаданную переменную пустой строкой: это
        # тот же невыбранный адаптер, а не ошибка формата.
        return None if value == "" else value

    @model_validator(mode="after")
    def _check_model_providers(self) -> Self:
        if self.llm_backend == "ollama" and not self.ollama_model:
            raise ValueError("OLLAMA_MODEL обязателен при LLM_BACKEND=ollama")
        if self.llm_hosted_fallback and self.llm_backend != "ollama":
            raise ValueError("LLM_HOSTED_FALLBACK требует LLM_BACKEND=ollama: фоллбэк - только после локальной модели")
        if not self.llm_hosted_fallback and self.llm_backend != "eurorouter":
            return self
        missing = [
            name
            for name, value in (
                ("EUROROUTER_BASE_URL", self.eurorouter_base_url),
                ("EUROROUTER_MODEL", self.eurorouter_model.strip()),
                ("EUROROUTER_API_KEYS", self.eurorouter_keys),
            )
            if not value
        ]
        if missing:
            needs = "LLM_BACKEND=eurorouter" if self.llm_backend == "eurorouter" else "LLM_HOSTED_FALLBACK=true"
            raise ValueError(f"{needs} требует {', '.join(missing)}")
        if not self.eurorouter_base_url.startswith("https://"):
            raise ValueError("EUROROUTER_BASE_URL - только https://: иначе код и ключи ушли бы открытым текстом")
        return self

    @property
    def llm_call_worst_case_seconds(self) -> float:
        """Худший случай одного вызова модели: таймаут × попытки × провайдеры."""
        if self.llm_backend not in ("ollama", "eurorouter"):
            return 0.0
        providers = 2 if self.llm_hosted_fallback else 1
        return self.llm_timeout_seconds * self.llm_max_attempts * providers

    @property
    def github_app_configured(self) -> bool:
        """Заданы ли идентификатор и приватный ключ GitHub App: без них JWT не подписать."""
        return bool(self.github_app_id and self.github_app_private_key.get_secret_value())

    @property
    def eurorouter_keys(self) -> tuple[SecretStr, ...]:
        """Ключи Eurorouter по одному, без пустых и пробелов вокруг."""
        raw = self.eurorouter_api_keys.get_secret_value()
        return tuple(SecretStr(key.strip()) for key in raw.split(",") if key.strip())


def _decode_token_key(value: str) -> bytes:
    """Ключ из base64 ровно 32 байт; иначе `ValueError` без значения ключа."""
    try:
        key = base64.b64decode(value.strip(), validate=True)
    except (binascii.Error, ValueError):
        raise ValueError("USER_TOKEN_ENCRYPTION_KEY — не base64 (openssl rand -base64 32)") from None
    if len(key) != USER_TOKEN_KEY_BYTES:
        raise ValueError(
            f"USER_TOKEN_ENCRYPTION_KEY — {USER_TOKEN_KEY_BYTES} байта в base64, а не {len(key)}"
            " (openssl rand -base64 32)"
        )
    return key


def load_settings() -> Settings:
    """Собрать настройки из окружения, громко упав, если какой-то не хватает."""
    return Settings()
