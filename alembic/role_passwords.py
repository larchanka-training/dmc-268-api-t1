"""Пароли ролей dmc268_monitor и dmc268_app — из окружения, после миграций.

Один шаг для деплоя (.github/scripts/deploy/migrate.sh) и сервиса migrate в
docker-compose.yml. Роли создают миграции (0013 и 0023); ревизия 0023 пароля не
задаёт, а ALTER ROLE здесь идемпотентен и сводит пароль с переменной на
каждом запуске — смена секрета доходит до роли обычным деплоем. Пароль —
параметром запроса, а не литералом команды: тот светится в /proc и docker
logs. На сервер уходит только SCRAM-хеш, как у `\\password` в passwords.sh:
упавший ALTER ROLE пишется в лог сервера вместе с текстом команды.
track_utility выключен той же транзакцией, чтобы и хеш не лёг в
pg_stat_statements.
"""

import os

import psycopg
from psycopg import sql

# Роль -> переменная с паролем. Имена ролей зашиты — те же, что в миграциях,
# env коллектора (dmc268_monitor) и DATABASE_URL API и воркера (dmc268_app).
ROLES = {
    "dmc268_monitor": "POSTGRES_MONITOR_PASSWORD",
    "dmc268_app": "POSTGRES_APP_PASSWORD",
}

# DATABASE_URL настроек — под схемой SQLAlchemy (postgresql+psycopg://);
# psycopg ждёт стандартную схему postgresql://. Привязка значений — на
# клиенте (ClientCursor): серверная не проходит по грамматике ALTER ROLE
# (PASSWORD принимает только литерал, не $1), а литерал руками собирать
# нельзя — psycopg экранирует сам.
dsn = os.environ["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://", 1)
with psycopg.connect(dsn, cursor_factory=psycopg.ClientCursor) as conn, conn.transaction():
    conn.execute("SET LOCAL pg_stat_statements.track_utility = off")
    for role, variable in ROLES.items():
        hashed = conn.pgconn.encrypt_password(os.environ[variable].encode(), role.encode(), b"scram-sha-256")
        conn.execute(sql.SQL("ALTER ROLE {} PASSWORD %s").format(sql.Identifier(role)), (hashed.decode(),))
