"""Исключения VCS-адаптеров.

Базовая шапка `VcsError` живёт в порту `app.application.ports.vcs_gateway`:
слой приложения ловит её, не нарушая слои (адаптер отсюда не импортируется
вверх, а порт не импортирует инфраструктуру). Здесь определены только
наследники, уточняющие причину сбоя для логирования и тестов.
"""

from app.application.ports.vcs_gateway import VcsError

__all__ = ["VcsAuthError", "VcsError", "VcsUnavailableError"]


class VcsUnavailableError(VcsError):
    """GitHub недоступен: обрыв сети (транспортный сбой httpx) или
    повторяемые сбои (429/5xx) исчерпали попытки адаптера."""


class VcsAuthError(VcsError):
    """Не удалось получить installation token."""
