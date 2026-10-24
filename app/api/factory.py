"""Фабрика приложения.

`app/main.py` остаётся тонкой точкой входа и лежит внутри пакета, чтобы его
видели контракты слоёв: они укоренены в `app`, а модуль в корне репозитория
находится вне проверяемого ими графа.
"""

import contextlib
import logging
from collections.abc import AsyncIterator, MutableMapping
from typing import Any

from fastapi import FastAPI

from app.config import Settings

_log = logging.getLogger(__name__)

_DESCRIPTION = """\
Контракт Backend API для запуска автоматического ревью Pull Request и получения состояния и
результатов. Схема строится из кода; `docs/openapi/openapi.json` — её экспорт.

Все пути — под `/api/v1`: префикс ставит само приложение, а не reverse-proxy. Ошибки —
RFC 9457 Problem Details (`application/problem+json`), клиент ветвится по `code`. На каждом
ответе — заголовок `X-Trace-ID`, в теле ошибки то же значение в `trace_id`.
"""

HEALTH_PATH = "/health"


def _is_health_check(scope: MutableMapping[str, Any]) -> bool:
    """Health-check — для Docker и деплоя: спаны и метрики на нём не нужны.

    Исключается целиком, опцией `exclude` встроенной телеметрии FastAPI. Спана нет,
    поэтому `X-Trace-ID` такого ответа — случайное значение из `TraceIdMiddleware`:
    проверка живости не для пользователей, связи с трейсом ей не нужно.
    """
    return scope["type"] == "http" and scope.get("path") == HEALTH_PATH


def create_app(settings: Settings | None = None) -> FastAPI:
    """Собрать ASGI-приложение.

    Явно переданные `settings` — это то, как тесты обходятся без настоящего
    окружения; без них настройки читаются из окружения и падают громко, если
    обязательной не хватает.
    """
    if settings is None:
        from app.config import load_settings

        settings = load_settings()

    from pydantic.alias_generators import to_camel

    from app.api import openapi
    from app.api.errors import ERRORS_DEFAULT
    from app.infrastructure import telemetry
    from app.infrastructure.container import build_container
    from app.infrastructure.logging import configure_logging

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # uvicorn на SIGTERM доводит lifespan до конца: спаны выгружаются здесь,
        # до выхода процесса, — и когда остановка прервана исключением.
        try:
            yield
        finally:
            await app.state.container.aclose()
            telemetry.shutdown_tracing()

    # Схема — из роутов и моделей (`app/api/openapi.py`); ответ `default` с каталогом
    # ошибок — у каждой операции. ReDoc не нужен — хватает одного просмотрщика.
    app = FastAPI(
        title="PR Review API",
        version="0.1.0",
        description=_DESCRIPTION,
        redoc_url=None,
        responses=ERRORS_DEFAULT,
        lifespan=lifespan,
        # Провайдеры ставит `configure_telemetry`. `auto_configure` при заданном
        # `OTEL_EXPORTER_OTLP_ENDPOINT` повесил бы на него второй экспортёр — выключен
        # явно, а не только отсутствием `FASTAPI_OTEL_AUTO_CONFIGURE`. Встроенный
        # OTel-лог исключений FastAPI не используем: стектрейс `500` пишет наш
        # обработчик (#89). Health-check исключён из телеметрии целиком (`_is_health_check`):
        # его опрашивают Docker и деплой, и спанов на нём быть не должно.
        telemetry={"auto_configure": False, "logs": False, "exclude": _is_health_check},
        # operationId — имя функции роута в camelCase (`list_repositories` → `listRepositories`):
        # по нему фронт находит операцию в сгенерированных типах.
        generate_unique_id_function=lambda route: to_camel(route.name),
    )
    from app.api.events import StreamEventType

    openapi.install(app, extra_models=[StreamEventType])
    app.state.settings = settings
    # Логи — до первого запроса и до трассировки: её предупреждения при старте
    # тоже должны выйти JSON-записями.
    configure_logging(telemetry.API_SERVICE)
    # До контейнера: провайдеры трейсов и метрик нужны телеметрии FastAPI с первого запроса.
    telemetry.configure_telemetry(telemetry.API_SERVICE)
    app.state.container = build_container(settings)
    if settings.user_token_key is None:
        # Видно в логе старта API: вход на таком стенде будет отказывать. Не в
        # контейнере — его собирает и воркер, которому ключ не передаётся.
        _log.warning("USER_TOKEN_ENCRYPTION_KEY не задан: вход отказывает, токены не хранятся")
    telemetry.instrument_engine(app.state.container.engine)
    telemetry.instrument_pool(
        app.state.container.engine, settings.db_pool_size, settings.db_max_overflow
    )

    from app.api import errors, log_redaction
    from app.api.auth import router as auth_router
    from app.api.events import router as events_router
    from app.api.origin import OriginMiddleware
    from app.api.pull_requests import router as pull_requests_router
    from app.api.repositories import router as repositories_router
    from app.api.reviews import router as reviews_router
    from app.api.trace_id import TraceIdMiddleware
    from app.api.webhooks import router as webhooks_router

    log_redaction.install()
    # Тот же query на путях входа скрыт и в серверном спане запроса.
    telemetry.redact_query(log_redaction.AUTH_PREFIX, log_redaction.REDACTED)
    errors.install(app)
    # Последний добавленный — внешний: trace id ставится раньше, чем Origin может отказать.
    app.add_middleware(OriginMiddleware, public_origin=settings.public_origin)
    app.add_middleware(TraceIdMiddleware)

    app.include_router(webhooks_router)
    app.include_router(auth_router)
    app.include_router(repositories_router)
    app.include_router(pull_requests_router)
    app.include_router(reviews_router)
    app.include_router(events_router)

    # Служебные ручки вне `/api/v1` — не часть контракта.
    @app.get("/", include_in_schema=False)
    def read_root() -> dict[str, str]:
        return {"message": "Welcome to DMC-268 Team 1 API"}

    @app.get(HEALTH_PATH, include_in_schema=False)
    def health_check() -> dict[str, str]:
        return {"status": "ok"}

    return app
