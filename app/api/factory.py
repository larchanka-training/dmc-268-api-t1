"""Application factory.

`app/main.py` stays a thin entrypoint, inside the package so the layering
contracts see it: they are rooted at `app`, and a module at the repository
root is outside the graph they check.
"""

from fastapi import FastAPI

from app.config import Settings


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the ASGI application.

    Passing `settings` explicitly is how tests avoid needing a real
    environment; omitting it loads from the environment and fails loudly when
    a required setting is missing.
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
