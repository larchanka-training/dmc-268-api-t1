"""Точка входа воркера: `python -m app.worker`.

Тонкая, как `app/main.py`: только собирает зависимости через `factory.py` и
запускает цикл. Обработчик — временная заглушка; `add-review-pipeline`
подключит сюда реальный доменный пайплайн.
"""

from app.config import load_settings
from app.worker.factory import build_worker


def _noop_handler(body: bytes) -> None:
    """Заглушка. Замещается доменным пайплайном в change'е add-review-pipeline."""


def main() -> None:
    build_worker(load_settings()).run(_noop_handler)


if __name__ == "__main__":
    main()
