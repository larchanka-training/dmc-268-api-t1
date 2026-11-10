"""Роль приложения dmc268_app (ревизия 0023): работа API и воркера есть, схемы нет.

API и воркер входят этой ролью (#101), поэтому тест идёт под ней настоящим
входом по паролю, а не `SET ROLE` суперпользователя: путь приложения —
адаптеры единицы работы, `pg_notify`, `LISTEN` и advisory lock плательщика —
проходит, а DDL, `TRUNCATE` и запись ревизии схемы — нет. Роль кластерная, и
другие тесты (откат 0013, хаос-тест миграций) могут снять её или сменить
пароль: миграция его не задаёт, фикстура ставит пароль сама перед входом.
"""

import asyncio
import datetime as dt
import os
import subprocess
import sys
from collections.abc import Iterator
from decimal import Decimal

import pytest
from alembic.config import Config
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ProgrammingError

from alembic import command
from app.application.review_pipeline import reserve_or_refuse
from app.domain.entities import BalanceCredit, MergeRequest, Repository, ReviewRun, User
from app.domain.enums import MergeRequestState, Provider, ReviewRunStatus, TriggerSource
from app.domain.ids import new_id
from app.infrastructure.db.run_changes import PostgresRunChanges, listen_conninfo
from app.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork

from ..conftest import TEST_DATABASE_URL, requires_db

pytestmark = [pytest.mark.integration, requires_db]

ROLE = "dmc268_app"
NOW = dt.datetime(2026, 11, 11, 12, 0, tzinfo=dt.UTC)


def _config() -> Config:
    assert TEST_DATABASE_URL is not None
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", TEST_DATABASE_URL.replace("%", "%%"))
    return config


@pytest.fixture
def app_url(clean_db) -> str:
    """URL тестовой базы под ролью приложения: пароль ставит тот же скрипт, что на деплое."""
    assert TEST_DATABASE_URL is not None
    # Роль кластерная: пароли те же, что в compose, иначе тест отрезал бы его api и воркер.
    password = "dmc268_app"
    env = os.environ | {
        "DATABASE_URL": TEST_DATABASE_URL,
        "POSTGRES_APP_PASSWORD": password,
        "POSTGRES_MONITOR_PASSWORD": "dmc268_monitor",
    }
    subprocess.run([sys.executable, "alembic/role_passwords.py"], env=env, check=True)
    url = make_url(TEST_DATABASE_URL).set(username=ROLE, password=password)
    return url.render_as_string(hide_password=False)


@pytest.fixture
def app_engine(app_url) -> Iterator[Engine]:
    engine = create_engine(app_url, future=True)
    yield engine
    engine.dispose()


def _seed(engine: Engine) -> tuple[User, ReviewRun]:
    payer = User(new_id(), Provider.GITHUB, "1", "alice", None, None, NOW, NOW)
    repository = Repository(
        id=new_id(), provider=Provider.GITHUB, provider_id="1", full_name="acme/api",
        default_branch="main", auto_review_enabled=True, created_at=NOW, updated_at=NOW,
        connected_at=NOW, payer_user_id=payer.id,
    )
    merge_request = MergeRequest(
        new_id(), repository.id, 1, "t", "", "a", "feat", "main", "abc123",
        MergeRequestState.OPEN, NOW, NOW,
    )
    run = ReviewRun(
        id=new_id(), merge_request_id=merge_request.id, head_sha="abc123",
        status=ReviewRunStatus.BUILDING_CONTEXT, trigger=TriggerSource.WEBHOOK,
        last_progress_at=NOW, created_at=NOW, updated_at=NOW,
    )
    with SqlAlchemyUnitOfWork(engine) as work:
        work.users.add(payer)
        work.repositories.add(repository)
        work.merge_requests.add(merge_request)
        work.review_runs.add(run)
        work.billing.add_credit(BalanceCredit(new_id(), payer.id, Decimal(1), "тест", "admin", NOW))
        work.commit()
    return payer, run


def test_the_role_logs_in_without_superuser_or_schema_rights(app_engine) -> None:
    with app_engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls"
                " FROM pg_roles WHERE rolname = current_user"
            )
        ).one()
        creates_in_schema = conn.execute(
            text("SELECT has_schema_privilege(current_schema(), 'CREATE')")
        ).scalar_one()
    assert tuple(row) == (False, False, False, False, False)
    assert not creates_in_schema


def test_the_app_path_works_under_the_role(app_url, app_engine) -> None:
    """Запись и чтение адаптерами, блокировка плательщика с UPDATE прогона и сигнал LISTEN."""
    payer, run = _seed(app_engine)

    async def scenario() -> tuple[ReviewRun, bool]:
        changes = PostgresRunChanges(listen_conninfo(app_url), reconnect_delay=0.1)
        try:
            subscription = await changes.subscribe()
            # pg_advisory_xact_lock плательщика, чтение баланса, UPDATE прогона
            # с FOR UPDATE и pg_notify — в одной транзакции, как у воркера.
            reserved = await asyncio.to_thread(
                reserve_or_refuse, SqlAlchemyUnitOfWork(app_engine), run,
                tokens=2000, max_output_tokens=500, now=NOW, duration_seconds=1.0,
            )
            return reserved, await subscription.wait(2.0)
        finally:
            await changes.close()

    reserved, signalled = asyncio.run(scenario())

    assert reserved.payer_user_id == payer.id
    assert signalled
    with SqlAlchemyUnitOfWork(app_engine) as work:
        stored = work.review_runs.get(run.id)
    assert stored is not None and stored.payer_user_id == payer.id


@pytest.mark.parametrize(
    "statement",
    [
        "CREATE TABLE app_role_probe (id int)",
        "ALTER TABLE review_runs ADD COLUMN app_role_probe int",
        "DROP TABLE findings",
        "TRUNCATE review_runs",
        "UPDATE alembic_version SET version_num = version_num",
    ],
)
def test_the_role_cannot_change_the_schema(app_engine, statement: str) -> None:
    with app_engine.connect() as conn, pytest.raises(ProgrammingError, match=r"permission denied|must be owner"):
        conn.execute(text(statement))


def test_a_table_of_a_later_revision_is_open_to_the_role(migrated, app_engine) -> None:
    """DEFAULT PRIVILEGES: таблица, созданная владельцем после 0023, доступна приложению без GRANT."""
    with migrated.begin() as conn:
        conn.execute(text("CREATE TABLE app_role_later (id int)"))
    try:
        with app_engine.begin() as conn:
            conn.execute(text("INSERT INTO app_role_later VALUES (1)"))
            assert conn.execute(text("SELECT count(*) FROM app_role_later")).scalar_one() == 1
    finally:
        with migrated.begin() as conn:
            conn.execute(text("DROP TABLE app_role_later"))


def _role_exists(engine: Engine) -> bool:
    with engine.connect() as conn:
        return conn.execute(text("SELECT 1 FROM pg_roles WHERE rolname = :n"), {"n": ROLE}).first() is not None


def test_the_revision_downgrades_and_upgrades_cleanly(migrated) -> None:
    """Откат снимает права и роль (если её не держат другие базы кластера), повтор — возвращает."""
    config = _config()
    with migrated.connect() as conn:
        other = conn.execute(
            text(
                "SELECT count(*) FROM pg_shdepend s JOIN pg_roles r ON r.oid = s.refobjid"
                " JOIN pg_database d ON d.oid = s.dbid"
                " WHERE s.refclassid = 'pg_authid'::regclass AND r.rolname = :n"
                " AND d.datname <> current_database()"
            ),
            {"n": ROLE},
        ).scalar_one()
    try:
        command.downgrade(config, "0022")
        with migrated.connect() as conn:
            grants = conn.execute(
                text(
                    "SELECT count(*) FROM information_schema.role_table_grants"
                    " WHERE grantee = :n"
                ),
                {"n": ROLE},
            ).scalar_one()
            default_acl = conn.execute(
                text(
                    "SELECT count(*) FROM pg_default_acl"
                    " WHERE array_to_string(defaclacl, ',') LIKE :pattern"
                ),
                {"pattern": f"%{ROLE}=%"},
            ).scalar_one()
        assert grants == 0
        assert default_acl == 0
        # Права в другой базе кластера (dev-база в make test-db) держат роль.
        assert _role_exists(migrated) == (other > 0)
    finally:
        command.upgrade(config, "head")
    assert _role_exists(migrated)
