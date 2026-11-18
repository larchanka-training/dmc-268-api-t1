"""Демо-данные для демо-режима: `python -m app.worker.seed_demo` (change `demo-mode`).

Запускается в образе приложения — `scripts/` туда не копируется, как и у
`app.worker.credit_balance`. В compose — одноразовый сервис `seed` после миграций.
Без `DEMO_MODE=true` ничего не пишет и выходит с кодом 0: так `docker compose up`
остаётся одной командой и при выключенном демо-режиме. Повторный запуск ничего не дублирует.
"""

import sys
from datetime import UTC, datetime

from app.application.use_cases.seed_demo import demo_dataset, seed_demo
from app.config import load_settings
from app.infrastructure.container import build_container


def main() -> int:
    settings = load_settings()
    if not settings.demo_mode:
        print("демо-режим выключен (DEMO_MODE): демо-данные не пишутся")
        return 0
    outcome = seed_demo(build_container(settings).unit_of_work(), demo_dataset(datetime.now(UTC)))
    print(f"демо-данные: записано репозиториев {outcome.repositories}, прогонов {outcome.runs}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
