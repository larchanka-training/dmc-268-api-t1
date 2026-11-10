"""Исключения VCS-адаптеров.

Базовая шапка `VcsError` живёт в домене (`app.domain.vcs_errors`): и слой
приложения, и адаптеры импортируют её оттуда, поэтому ни один из них не
зависит от другого. Здесь определены только наследники, уточняющие причину
сбоя для логирования и тестов. `VcsNotFoundError` тоже доменный — его ловит
слой приложения (дифф прогона); здесь он реэкспортирован, чтобы импорты
адаптеров и тестов не менялись.
"""

from typing import ClassVar

from app.domain.vcs_errors import VcsError, VcsNotFoundError, VcsTransientError

__all__ = [
    "VcsAuthError",
    "VcsError",
    "VcsNotFoundError",
    "VcsUnavailableError",
]


class VcsUnavailableError(VcsTransientError):
    """GitHub недоступен: обрыв сети (транспортный сбой httpx) или
    повторяемые сбои (429/5xx) исчерпали попытки адаптера."""


class VcsAuthError(VcsError):
    """Ошибка аутентификации: не удалось получить installation token
    или выданный токен отклонён GitHub'ом."""

    code: ClassVar[str] = "SCM_AUTH_ERROR"
