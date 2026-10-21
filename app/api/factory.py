"""Фабрика приложения.

`app/main.py` остаётся тонкой точкой входа и лежит внутри пакета, чтобы его
видели контракты слоёв: они укоренены в `app`, а модуль в корне репозитория
находится вне проверяемого ими графа.
"""

from fastapi import FastAPI

from app.config import Settings

_DESCRIPTION = """\
Контракт Backend API для запуска автоматического ревью Pull Request и получения состояния и
результатов. Схема строится из кода; `docs/openapi/openapi.json` — её экспорт.

Все пути — под `/api/v1`: префикс ставит само приложение, а не reverse-proxy. Ошибки —
RFC 9457 Problem Details (`application/problem+json`), клиент ветвится по `code`. На каждом
ответе — заголовок `X-Trace-ID`, в теле ошибки то же значение в `trace_id`.
"""


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

    # Схема — из роутов и моделей (`app/api/openapi.py`); ответ `default` с каталогом
    # ошибок — у каждой операции. ReDoc не нужен — хватает одного просмотрщика.
    app = FastAPI(
        title="PR Review API",
        version="0.1.0",
        description=_DESCRIPTION,
        redoc_url=None,
        responses=ERRORS_DEFAULT,
        # operationId — имя функции роута в camelCase (`list_repositories` → `listRepositories`):
        # по нему фронт находит операцию в сгенерированных типах.
        generate_unique_id_function=lambda route: to_camel(route.name),
    )
    from app.api.review_events import ReviewRunStatusEvent

    openapi.install(app, extra_models=[ReviewRunStatusEvent])
    app.state.settings = settings
    # До контейнера: провайдер трейсов нужен телеметрии FastAPI с первого запроса.
    telemetry.configure_tracing(telemetry.API_SERVICE)
    app.state.container = build_container(settings)
    telemetry.instrument_engine(app.state.container.engine)

    from app.api import errors, log_redaction
    from app.api.auth import router as auth_router
    from app.api.origin import OriginMiddleware
    from app.api.pull_requests import router as pull_requests_router
    from app.api.repositories import router as repositories_router
    from app.api.review_events import router as review_events_router
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
    app.include_router(review_events_router)

    # Служебные ручки вне `/api/v1` — не часть контракта.
    @app.get("/", include_in_schema=False)
    def read_root() -> dict[str, str]:
        return {"message": "Welcome to DMC-268 Team 1 API"}

    @app.get("/health", include_in_schema=False)
    def health_check() -> dict[str, str]:
        return {"status": "ok"}

    return app
