"""Ревизия 0013 откатывается начисто и накатывается снова: роль dmc268_monitor.

Роль и расширение нужны метрикам базы (receiver postgresql коллектора,
решение #89): роль только для входа с привилегией pg_monitor и
pg_stat_statements. Отличие от табличных ревизий: роли кластерные —
состояние видно всем базам инстанции, поэтому тест снимает роль перед
проверкой, чтобы upgrade начинался с чистого состояния даже там, где
другая база уже мигрирована.
"""

import os
import uuid

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError, ProgrammingError

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
        # Права роли на колонки review_runs (ревизия 0017) держат её: DROP ROLE
        # с ними падает. DROP OWNED BY отзывает их в этой базе; у отсутствующей
        # роли он сам — ошибка, поэтому только при её наличии.
        exists = conn.execute(
            text("SELECT 1 FROM pg_roles WHERE rolname = :name"), {"name": ROLE}
        ).first()
        if exists is not None:
            conn.execute(text(f"DROP OWNED BY {ROLE}"))
        conn.execute(text(f"DROP ROLE IF EXISTS {ROLE}"))


def _role_accepts_password(password: str) -> bool:
    assert TEST_DATABASE_URL is not None
    # Тот же сервер, что у тестовой базы, но под ролью мониторинга: вход
    # проходит, если роль создана миграцией с этим паролем. Локальный стек
    # держит host-авторизацию в trust — там проверка ничего не отвергнет и не
    # даст ложного срабатывания; отвергнуть она может там, где пароль реально
    # проверяется (CI-сервис PostgreSQL).
    url = make_url(TEST_DATABASE_URL).set(
        username=ROLE, password=password
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
        assert _role_accepts_password(DEFAULT_ROLE_PASSWORD)


def _canary_leaks(migrated, fragment_first: str, fragment_last: str) -> int:
    """Сколько записей pg_stat_statements содержит склеенную канарейку.

    Литерал собирается из частей bind-параметрами: сам запрос проверки не
    должен попасть в статистику с канарейкой целиком — иначе он находил бы
    собственную запись.
    """
    with migrated.connect() as conn:
        return int(
            conn.execute(
                text(
                    "SELECT count(*) FROM pg_stat_statements"
                    " WHERE query LIKE '%' || :first || :last || '%'"
                ),
                {"first": fragment_first, "last": fragment_last},
            ).scalar_one()
        )


def test_the_role_password_never_reaches_pg_stat_statements(migrated) -> None:
    """Пароль роли из ревизии 0013 не оседает в pg_stat_statements.

    Утилитные выражения трекер пишет как есть — константы не заменяются
    плейсхолдерами (проверено на PostgreSQL 18), поэтому пароль из литерала
    ALTER ROLE уехал бы в статистику сервера открытым текстом. Ревизия
    выключает track_utility до ALTER ROLE в той же транзакции (SET LOCAL).
    Контрольная половина в конце доказывает, что проверка не пустая: с
    включённым track_utility тот же ALTER ROLE записывается и находится.
    """
    config = _config()
    # Роли кластерные: снимаем роль и откатываем ревизию, чтобы upgrade
    # прошёл заново — с паролем-канарейкой из окружения.
    _drop_role(migrated)
    command.downgrade(config, "0012")
    assert not _extension_exists(migrated)

    canary = f"canary-{uuid.uuid4().hex}"
    previous = os.environ.pop("POSTGRES_MONITOR_PASSWORD", None)
    os.environ["POSTGRES_MONITOR_PASSWORD"] = canary
    try:
        command.upgrade(config, "head")
    finally:
        # Остальные тесты сессии ждут окружение без канарейки.
        if previous is None:
            os.environ.pop("POSTGRES_MONITOR_PASSWORD", None)
        else:
            os.environ["POSTGRES_MONITOR_PASSWORD"] = previous

    assert _extension_exists(migrated)
    # Роль приняла канарейку: ALTER ROLE прошёл именно с паролем из окружения
    # (где host-авторизация проверяет пароль по-настоящему).
    assert _role_accepts_password(canary)
    # Статистика жива: записи пишутся (этот SELECT уже попал в неё сам).
    with migrated.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM pg_stat_statements")).scalar_one() > 0
    # И канарейки в ней нет — track_utility был выключен той же транзакцией.
    assert _canary_leaks(migrated, "canary-", canary.removeprefix("canary-")) == 0

    # Контроль: тот же ALTER ROLE с включённым track_utility записывается —
    # значит, отсутствие канарейки выше достигнуто выключением трекера, а не
    # тем, что статистика перестала писать ALTER ROLE. Канарейка из uuid:
    # конфликты со старыми записями статистики исключены. Привязка — литералом:
    # серверная привязка psycopg не проходит по грамматике PASSWORD (как и в
    # самой ревизии), символы из uuid.hex литерал не ломают.
    control = f"control-{uuid.uuid4().hex}"
    with migrated.begin() as conn:
        conn.execute(text("SET pg_stat_statements.track_utility = on"))
        conn.execute(text(f"ALTER ROLE {ROLE} PASSWORD '{control}'"))
        # Соединение вернётся в пул с настройкой по умолчанию.
        conn.execute(text("RESET pg_stat_statements.track_utility"))
    assert _canary_leaks(migrated, "control-", control.removeprefix("control-")) == 1

    # Пароль роли возвращаем к дефолту тем же приёмом, что и деплой
    # (infra/main.tf): track_utility выключен в той же транзакции.
    with migrated.begin() as conn:
        conn.execute(text("SET pg_stat_statements.track_utility = off"))
        conn.execute(
            text(f"ALTER ROLE {ROLE} PASSWORD '{DEFAULT_ROLE_PASSWORD}'")
        )
        conn.execute(text("RESET pg_stat_statements.track_utility"))


def test_the_monitor_role_reads_only_the_token_columns_of_runs(migrated) -> None:
    """Ревизия 0017: роль мониторинга читает расход токенов, но не прогоны.

    Запрос receiver-а sqlquery коллектора (infra/observability/otelcol.yaml)
    проходит под ролью, а чтение любой другой колонки review_runs — нет.
    """
    with migrated.connect() as conn, conn.begin():
        conn.execute(text(f"SET LOCAL ROLE {ROLE}"))
        tokens = conn.execute(
            text(
                "SELECT coalesce(sum(tokens_used), 0)::bigint FROM review_runs"
                " WHERE created_at >= date_trunc('month', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'"
            )
        ).scalar_one()
    assert tokens >= 0

    # Отказ прерывает транзакцию: откатываем её явно, а не коммитом на выходе.
    with migrated.connect() as conn:
        transaction = conn.begin()
        try:
            conn.execute(text(f"SET LOCAL ROLE {ROLE}"))
            with pytest.raises(ProgrammingError, match="permission denied"):
                conn.execute(text("SELECT id FROM review_runs LIMIT 1"))
        finally:
            transaction.rollback()


def test_the_token_grant_revision_downgrades_and_upgrades_cleanly(migrated) -> None:
    config = _config()
    try:
        command.downgrade(config, "0016")
        with migrated.connect() as conn:
            granted = conn.execute(
                text(
                    "SELECT count(*) FROM information_schema.column_privileges"
                    " WHERE grantee = :name AND table_name = 'review_runs'"
                ),
                {"name": ROLE},
            ).scalar_one()
        assert granted == 0
    finally:
        command.upgrade(config, "head")
