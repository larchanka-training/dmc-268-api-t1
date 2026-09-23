"""Фабрика приложения.

`app/main.py` остаётся тонкой точкой входа и лежит внутри пакета, чтобы его
видели контракты слоёв: они укоренены в `app`, а модуль в корне репозитория
находится вне проверяемого ими графа.
"""

from fastapi import FastAPI

from app.config import Settings


def create_app(settings: Settings | None = None) -> FastAPI:
    """Собрать ASGI-приложение.

    Явно переданные `settings` — это то, как тесты обходятся без настоящего
    окружения; без них настройки читаются из окружения и падают громко, если
    обязательной не хватает.
    """
    if settings is None:
        from app.config import load_settings

        settings = load_settings()

    from app.infrastructure.container import build_container

    app = FastAPI(title="DMC-268 API", version="0.1.0")
    app.state.settings = settings
    app.state.container = build_container(settings)

    @app.get("/")
    def read_root() -> dict[str, str]:
        return {"message": "Welcome to DMC-268 Team 1 API"}

    @app.get("/health")
    def health_check() -> dict[str, str]:
        return {"status": "ok"}

    return app
