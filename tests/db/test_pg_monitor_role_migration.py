"""Ревизия 0013 откатывается начисто и накатывается снова: роль dmc268_monitor.

Роль и расширение нужны метрикам базы (receiver postgresql коллектора,
решение #89): роль только для входа с привилегией pg_monitor и
pg_stat_statements. Отличие от табличных ревизий: роли кластерные —
состояние видно всем базам инстанции, поэтому тест снимает роль перед
проверкой, чтобы upgrade начинался с чистого состояния даже там, где
другая база уже мигрирована.
"""

import os

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError

from alembic import command

from ..conftest import TEST_DATABASE_URL, requires_db

pytestmark = [pytest.mark.integration, requires_db]

ROLE = "dmc268_monitor"
EXTENSION = "pg_stat_statements"
# Пароль роли — дефолт миграции: на стенде его задаёт окружение, тест
# проверяет локальный путь (POSTGRES_MONITOR_PASSWORD не задан).
DEFAULT_ROLE_PASSWORD = "dmc268_monitor"


def _config() -> Config:
    assert TEST_DATABASE_URL is not None
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", TEST_DATABASE_URL.replace("%", "%%"))
    return config


def _role_exists(engine) -> bool:
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT rolsuper, rolcanlogin FROM pg_roles WHERE rolname = :name"),
            {"name": ROLE},
        ).one_or_none()
    return row is not None


def _role_is_monitor_member(engine) -> bool:
    """Членство в pg_monitor по pg_auth_members, а не по pg_has_role: явная
    таблица членства не зависит от текущего пользователя соединения."""
    with engine.connect() as conn:
        count: int = conn.execute(
            text(
                "SELECT count(*) FROM pg_auth_members m"
                " JOIN pg_roles granted ON granted.oid = m.roleid"
                " JOIN pg_roles member ON member.oid = m.member"
                " WHERE granted.rolname = 'pg_monitor' AND member.rolname = :name"
            ),
            {"name": ROLE},
        ).scalar_one()
    return count == 1


def _extension_exists(engine) -> bool:
    with engine.connect() as conn:
        count: int = conn.execute(
            text("SELECT count(*) FROM pg_extension WHERE extname = :name"),
            {"name": EXTENSION},
        ).scalar_one()
    return count == 1


def _drop_role(engine) -> None:
    with engine.begin() as conn:
        conn.execute(text(f"DROP ROLE IF EXISTS {ROLE}"))


def _role_accepts_the_default_password() -> bool:
    assert TEST_DATABASE_URL is not None
    # Тот же сервер, что у тестовой базы, но под ролью мониторинга: роль
    # создана миграцией с дефолтным паролем, если вход проходит.
    url = make_url(TEST_DATABASE_URL).set(
        username=ROLE, password=DEFAULT_ROLE_PASSWORD
    )
    try:
        with create_engine(url, connect_args={"connect_timeout": 3}).connect() as conn:
            conn.execute(text("SELECT 1"))
    except OperationalError:
        return False
    return True


def test_the_monitor_role_revision_downgrades_and_upgrades_cleanly(migrated) -> None:
    config = _config()
    # Роли кластерные: миграция другой базы инстанции могла оставить роль —
    # иначе upgrade в тесте упал бы на «role already exists».
    _drop_role(migrated)
    try:
        command.downgrade(config, "0012")
        assert not _role_exists(migrated)
        assert not _extension_exists(migrated)
    finally:
        # Остальные тесты сессии ждут базу на head.
        command.upgrade(config, "head")

    with migrated.connect() as conn:
        rolsuper, rolcanlogin = conn.execute(
            text("SELECT rolsuper, rolcanlogin FROM pg_roles WHERE rolname = :name"),
            {"name": ROLE},
        ).one()
    # Роль только для входа, без суперпользователя: метрики, не данные.
    assert rolcanlogin
    assert not rolsuper
    assert _role_is_monitor_member(migrated)
    assert _extension_exists(migrated)
    # Пароль — дефолт миграции, если окружение его не переопределило.
    if not os.environ.get("POSTGRES_MONITOR_PASSWORD"):
        assert _role_accepts_the_default_password()
