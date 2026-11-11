"""Пароли ролей dmc268_monitor и dmc268_app — из окружения, после миграций.

Заодно снова закрывает alembic_version от роли приложения: после pg_restore --clean
таблица пересоздана и по DEFAULT PRIVILEGES снова открыта ей, а этот шаг идёт на
каждом деплое.

Один шаг для деплоя (.github/scripts/deploy/migrate.sh) и сервиса migrate в
docker-compose.yml. Роли создают миграции (0013 и 0023); ревизия 0023 пароля не
задаёт, а ALTER ROLE здесь идемпотентен и сводит пароль с переменной на
каждом запуске — смена секрета доходит до роли обычным деплоем. Пароль —
параметром запроса, а не литералом команды: тот светится в /proc и docker
logs. На сервер уходит только SCRAM-хеш, как у `\\password` в passwords.sh:
упавший ALTER ROLE пишется в лог сервера вместе с текстом команды.
track_utility выключен той же транзакцией, чтобы и хеш не лёг в
pg_stat_statements.

Сменился ли пароль, видно по входу: роль входит секретом — «совпадает», нет —
в stdout строка «… — меняю», до ALTER ROLE. По ней migrate.sh пишет
`postgres-app` в `passwords-changed` (как passwords.sh, раньше самой смены).
"""

import os

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo

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


def signs_in(role: str, password: str) -> bool:
    try:
        psycopg.connect(make_conninfo(dsn, user=role, password=password, connect_timeout=10)).close()
    except psycopg.OperationalError:
        return False
    return True


# Владельцем — до проб: недоступная база падает здесь, а не выдаёт себя за смену пароля.
with psycopg.connect(dsn, cursor_factory=psycopg.ClientCursor) as conn:
    for role, variable in ROLES.items():
        if signs_in(role, os.environ[variable]):
            print(f"{role}: пароль совпадает с секретом", flush=True)
        else:
            print(f"{role}: пароль не совпадает с секретом — меняю", flush=True)
    with conn.transaction():
        conn.execute("SET LOCAL pg_stat_statements.track_utility = off")
        for role, variable in ROLES.items():
            hashed = conn.pgconn.encrypt_password(os.environ[variable].encode(), role.encode(), b"scram-sha-256")
            conn.execute(sql.SQL("ALTER ROLE {} PASSWORD %s").format(sql.Identifier(role)), (hashed.decode(),))
        conn.execute("REVOKE ALL ON alembic_version FROM dmc268_app")
