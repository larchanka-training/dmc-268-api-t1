"""роль dmc268_app: API и воркер входят в базу без суперпользователя

До этой ревизии API и воркер входили ролью `dmc268` — владельцем базы и
суперпользователем кластера (`POSTGRES_USER` образа): ошибка или инъекция в
приложении могла бы менять схему, читать файлы сервера и роли кластера (#101).
Теперь они входят ролью `dmc268_app` только с DML: SELECT, INSERT, UPDATE,
DELETE на таблицы и USAGE, SELECT на последовательности схемы. Миграции
по-прежнему идут владельцем.

Revision ID: 0023
Revises: 0022
Create Date: 2026-11-11 12:00:00.000000

"""
from collections.abc import Sequence

from alembic import op

# Идентификаторы ревизии, используются Alembic.
revision: str = '0023'
down_revision: str | Sequence[str] | None = '0022'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Имя роли зашито: под ним API и воркер входят в базу (DATABASE_URL в
# infra/locals.tf и docker-compose.yml) — переименовать можно только вместе с ними.
_APP_ROLE = "dmc268_app"


def upgrade() -> None:
    """Роль без прав на схему, с DML на таблицы — и на те, что создадут следующие ревизии."""
    # Роль кластерная: на второй базе того же кластера она уже есть.
    op.execute(
        f"DO $$ BEGIN"
        f" IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{_APP_ROLE}')"
        f" THEN CREATE ROLE {_APP_ROLE}; END IF;"
        f" END $$"
    )
    # Пароль миграция не задаёт: сразу после upgrade его ставит alembic/role_passwords.py.
    op.execute(
        f"ALTER ROLE {_APP_ROLE} WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE"
        f" NOREPLICATION NOBYPASSRLS"
    )
    # Схема — текущая, где миграции создают таблицы (как в ревизии 0017).
    # DEFAULT PRIVILEGES — для объектов, которые создаст текущая роль, то есть
    # владелец, под которым идут миграции: таблица следующей ревизии получит
    # DML для приложения без отдельного GRANT. alembic_version приложению не
    # нужна: ревизию базы двигают только миграции.
    op.execute(
        f"DO $$ BEGIN"
        f" EXECUTE format('GRANT CONNECT ON DATABASE %I TO {_APP_ROLE}', current_database());"
        f" EXECUTE format('GRANT USAGE ON SCHEMA %I TO {_APP_ROLE}', current_schema());"
        f" EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA %I TO {_APP_ROLE}', current_schema());"
        f" EXECUTE format('GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA %I TO {_APP_ROLE}', current_schema());"
        f" EXECUTE format('ALTER DEFAULT PRIVILEGES IN SCHEMA %I GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {_APP_ROLE}', current_schema());"
        f" EXECUTE format('ALTER DEFAULT PRIVILEGES IN SCHEMA %I GRANT USAGE, SELECT ON SEQUENCES TO {_APP_ROLE}', current_schema());"
        f" EXECUTE format('REVOKE ALL ON %I.alembic_version FROM {_APP_ROLE}', current_schema());"
        f" END $$"
    )


def downgrade() -> None:
    """Отозвать права в этой базе и снять роль, если её не держат другие базы кластера.

    Роль могли снять до отката (так делает тест ревизии): REVOKE у
    несуществующей роли — ошибка, поэтому всё — только при её наличии.
    """
    op.execute(
        f"DO $$ BEGIN"
        f" IF EXISTS (SELECT FROM pg_roles WHERE rolname = '{_APP_ROLE}') THEN"
        f" EXECUTE format('ALTER DEFAULT PRIVILEGES IN SCHEMA %I REVOKE ALL ON TABLES FROM {_APP_ROLE}', current_schema());"
        f" EXECUTE format('ALTER DEFAULT PRIVILEGES IN SCHEMA %I REVOKE ALL ON SEQUENCES FROM {_APP_ROLE}', current_schema());"
        f" EXECUTE format('REVOKE ALL ON ALL TABLES IN SCHEMA %I FROM {_APP_ROLE}', current_schema());"
        f" EXECUTE format('REVOKE ALL ON ALL SEQUENCES IN SCHEMA %I FROM {_APP_ROLE}', current_schema());"
        f" EXECUTE format('REVOKE USAGE ON SCHEMA %I FROM {_APP_ROLE}', current_schema());"
        f" EXECUTE format('REVOKE CONNECT ON DATABASE %I FROM {_APP_ROLE}', current_database());"
        # Права в другой базе того же кластера отсюда не снять, и DROP ROLE
        # упал бы на них: тогда роль остаётся той базе (как у отката 0013).
        f" IF NOT EXISTS (SELECT FROM pg_shdepend s"
        f" JOIN pg_roles r ON r.oid = s.refobjid"
        f" JOIN pg_database d ON d.oid = s.dbid"
        f" WHERE s.refclassid = 'pg_authid'::regclass"
        f" AND r.rolname = '{_APP_ROLE}'"
        f" AND d.datname <> current_database())"
        f" THEN DROP ROLE {_APP_ROLE}; END IF;"
        f" END IF;"
        f" END $$"
    )
