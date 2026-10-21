"""Исключения VCS-адаптеров.

Базовая шапка `VcsError` живёт в домене (`app.domain.vcs_errors`): и слой
приложения, и адаптеры импортируют её оттуда, поэтому ни один из них не
зависит от другого. Здесь определены только наследники, уточняющие причину
сбоя для логирования и тестов.
"""

from typing import ClassVar

from app.domain.vcs_errors import VcsError

__all__ = ["VcsAuthError", "VcsError", "VcsNotFoundError", "VcsUnavailableError"]


class VcsUnavailableError(VcsError):
    """GitHub недоступен: обрыв сети (транспортный сбой httpx) или
    повторяемые сбои (429/5xx) исчерпали попытки адаптера."""


class VcsAuthError(VcsError):
    """Ошибка аутентификации: не удалось получить installation token
    или выданный токен отклонён GitHub'ом."""

    code: ClassVar[str] = "SCM_AUTH_ERROR"


class VcsNotFoundError(VcsError):
    """GitHub ответил 404. Для диффа и метаданных — сбой, для `fetch_file`
    — штатный исход «файла на коммите нет», который адаптер переводит в
    `None`.

    Код — `PR_NOT_FOUND`: 404 на `compare` GitHub отдаёт одним телом и на
    неизвестный коммит (force-push убрал `head_sha`), и на репозиторий,
    которого нет или к которому у установки App больше нет доступа. Отличить
    `REPOSITORY_NOT_FOUND` можно только лишним запросом к репозиторию;
    дифф прогона ищется по коммитам PR, поэтому выбран код PR.
    """

    code: ClassVar[str] = "PR_NOT_FOUND"
