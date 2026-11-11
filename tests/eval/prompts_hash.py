"""Хэш текстов промптов ревью для отчётов eval (LLM_EVAL §5).

Отдельный лёгкий модуль: офлайн-eval золотого датасета берёт хэш отсюда и не
тянет за собой сквозной стенд eval правил.
"""

import hashlib

from app.infrastructure.prompts import DEFAULT_PROMPTS_DIR


def prompts_sha256() -> str:
    """Хэш текстов промптов: метрики сравнимы только при одном промпте (LLM_EVAL §5)."""
    digest = hashlib.sha256()
    for path in sorted(DEFAULT_PROMPTS_DIR.glob("*.md")):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()
