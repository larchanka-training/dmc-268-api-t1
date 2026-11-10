"""Пароль роли мониторинга dmc268_monitor — из POSTGRES_MONITOR_PASSWORD, после миграций.

Один шаг для деплоя (.github/scripts/deploy/migrate.sh) и сервиса migrate в
docker-compose.yml. ALTER ROLE идемпотентен: пароль сводится с переменной на
каждом запуске. Пароль — параметром запроса, а не литералом команды: тот
светится в /proc и docker logs; track_utility выключен той же транзакцией —
иначе ALTER ROLE ... PASSWORD лёг бы в pg_stat_statements открытым текстом.
"""

import os

import psycopg

# DATABASE_URL настроек — под схемой SQLAlchemy (postgresql+psycopg://);
# psycopg ждёт стандартную схему postgresql://. Привязка значений — на
# клиенте (ClientCursor): серверная не проходит по грамматике ALTER ROLE
# (PASSWORD принимает только литерал, не $1), а литерал руками собирать
# нельзя — psycopg экранирует сам.
dsn = os.environ["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://", 1)
with psycopg.connect(dsn, cursor_factory=psycopg.ClientCursor) as conn, conn.transaction():
    conn.execute("SET LOCAL pg_stat_statements.track_utility = off")
    # Имя роли зашито — то же, что в миграции 0013 и env коллектора.
    conn.execute(
        "ALTER ROLE dmc268_monitor PASSWORD %s",
        (os.environ["POSTGRES_MONITOR_PASSWORD"],),
    )
