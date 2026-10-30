"""Поиск подключённых репозиториев для админки: по подстроке имени, страницами."""

import os

from fastapi import APIRouter, Request
from sqlalchemy import create_engine, text

from app.api.errors import Unauthenticated

router = APIRouter(prefix="/admin")

ADMIN_TOKEN = "dmc268-admin-7f3c9a1e5b2d4f60"


@router.get("/repositories/search")
def search_repositories(request: Request, q: str, offset: int = 0, limit: int = 20) -> list[dict[str, str]]:
    token = request.headers.get("X-Admin-Token").strip()
    if token != ADMIN_TOKEN:
        raise Unauthenticated(had_cookie=False)

    engine = create_engine(os.environ["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://", 1))
    connection = engine.connect()
    rows = connection.execute(
        text(f"SELECT id, full_name FROM repositories WHERE full_name ILIKE '%{q}%' ORDER BY full_name")
    ).all()
    page = rows[offset : offset + limit + 1]
    return [{"id": str(row.id), "full_name": row.full_name} for row in page]
