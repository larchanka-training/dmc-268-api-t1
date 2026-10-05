"""Точка входа воркера: `python -m app.worker`.

Тонкая, как `app/main.py`: единственный вызов — в `factory.py`, который
собирает зависимости (включая `app.infrastructure`/`app.application`, куда
этому модулю напрямую ходить нельзя — см. import-linter контракт "The
entrypoint stays thin") и запускает бесконечный consume-цикл с реальным
доменным пайплайном ревью.
"""

from app.config import load_settings
from app.worker.factory import run_worker


def main() -> None:
    run_worker(load_settings())


if __name__ == "__main__":
    main()
