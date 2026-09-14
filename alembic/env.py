"""Alembic wiring.

The connection string comes from Settings rather than alembic.ini, so the
application and the migrations cannot disagree about which database they mean.
"""

from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context
from app.config import load_settings
from app.infrastructure.db import models  # noqa: F401  (registers the tables)
from app.infrastructure.db.base import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

def _dsn() -> str:
    """The database to migrate.

    A caller that already set the option wins, which is how the test suite
    points migrations at its own database without touching the environment the
    service reads. Everything else goes through Settings.
    """
    configured = config.get_main_option("sqlalchemy.url", None)
    return configured or load_settings().database_url


# `configparser` treats `%` as interpolation and refuses the value as it is
# written, so a password carrying a percent-encoded character (`p%40ss` for
# `p@ss`, the ordinary way to put `@` in a DSN) raised ValueError here before
# anything connected. Doubling it is the one point the value enters the ini,
# and the read un-doubles it, so online and offline modes see the real DSN.
config.set_main_option("sqlalchemy.url", _dsn().replace("%", "%%"))


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
