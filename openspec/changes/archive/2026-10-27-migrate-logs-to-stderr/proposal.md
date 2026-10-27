# Proposal

## Why

У alembic CLI stdout — результат команды (`current`, `heads`, `history`), его читают скрипты
деплоя. С JSON-логами миграций (#130) `configure_logging` пишет их в тот же stdout, и перед
ревизией в выводе `alembic current` идут строки `{"timestamp": …}`. В #130 это закрыто фильтром в
`current_revision` (`scripts/deploy_report.py`), но фильтр чинит одного читателя: следующий скрипт,
читающий stdout alembic, наступит на то же (ревью #130, Myrhoiazov). Закрывает #232.

## What Changes

- `configure_logging(service_name, stream=None)`: необязательный поток вместо stdout; без него
  хендлер остаётся на прежнем потоке.
- `alembic/env.py` передаёт `sys.stderr`: логи миграций — тем же JSON, но в stderr.
- `migrate.sh`: upgrade и так собирается `2>&1` — в лог шага и `migrate.log` записи попадают как
  раньше; `ask_current` кладёт в `migrate-current.log` только stdout, то есть только ревизию.
- Фильтр JSON-строк в `current_revision` остаётся страховкой.
- Комментарии в `migrate.sh` около строк 104 и 118 перенесены по ширине (нит ревью #130).

## Capabilities

### New Capabilities

Нет.

### Modified Capabilities

- `observability`: «Логи — JSON с trace-контекстом» больше не называет поток; новое требование «Поток логов» — API и воркер в stdout, миграции в stderr (отдельно, чтобы текст требования не превысил лимит `openspec validate --strict`).

## Impact

- `app/infrastructure/logging.py`, `alembic/env.py`, `.github/scripts/deploy/migrate.sh`,
  `scripts/deploy_report.py` (докстринг).
- Тесты: `tests/infrastructure/test_logging.py`, `tests/db/test_alembic_env.py`,
  `tests/test_deploy_report.py` (комментарий).
- Документы: `docs/BACKEND_ARCHITECTURE.md`, `.agents/rules/backend.md`.
- API и воркер не меняются: их логи по-прежнему в stdout.
