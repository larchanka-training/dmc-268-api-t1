"""Зарегистрированные репозитории: чтение, подключение, настройки и статус `.review/rules.md`
(настройки и правила — пока заглушки).

Транспорт: разбор запроса, вызов порта или use case, формат ответа. Пагинация —
`app/api/pagination.py`. Правила подключения — `app/application/use_cases/connect_repository.py`.
"""

from collections.abc import Callable
from datetime import UTC, datetime
from http import HTTPStatus
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Path
from pydantic import BaseModel, Field, RootModel, StringConstraints

from app.api.dependencies import (
    get_installation_catalog,
    get_unit_of_work,
    get_unit_of_work_factory,
)
from app.api.errors import (
    ERRORS_403_ORIGIN_OR_MANAGE,
    ERRORS_404_NOT_IN_INSTALLATION,
    ERRORS_404_REPOSITORY,
    ERRORS_409_ALREADY_CONNECTED,
    ERRORS_422,
    ERRORS_501_NOT_IMPLEMENTED,
    ERRORS_502_SCM_REPOSITORIES,
    ApiError,
)
from app.api.pagination import DEFAULT_LIMIT, Limit, Offset, page_size
from app.api.problems import ErrorCode
from app.api.schemas import RepositoryBase, StrictRequest, UtcDatetime
from app.api.session import SESSION_REQUIRED, SESSION_RESPONSES, current_user
from app.application.ports import InstallationCatalog, UnitOfWork

# Под своими именами: имена функций роутов — operationId в схеме, их держат типы фронтенда.
from app.application.use_cases import connect_repository as connecting
from app.domain.entities import User
from app.domain.enums import FindingSeverity, Provider, RulesStatus
from app.domain.ids import new_id
from app.domain.repo_rules import RULES_PATH
from app.domain.vcs_errors import VcsError

# Форма — вариант A решения 3 в dmc-268-ui-t1#37; поведение — change `implement-repository-connect`.
_AVAILABLE_DESCRIPTION = (
    "Требует сессию. Репозитории установок GitHub App, к которым у вошедшего пользователя есть "
    "доступ в GitHub (любая роль, в том числе read), с отметкой `already_connected` — "
    "зарегистрирован ли репозиторий в сервисе. Пользователь видит только то, что и так видит в "
    "GitHub; остальные репозитории установок в ответ не попадают, это не ошибка. Подключить из "
    "списка можно только с ролью admin или maintain, иначе подключение ответит "
    "`403 REPOSITORY_MANAGE_REQUIRED`. Роль проверяется запросом к GitHub на каждый репозиторий "
    "установок. Если GitHub не ответил или ответил ошибкой, в том числе на запрос роли, — "
    "`502 SCM_UNAVAILABLE`."
)
_CONNECT_DESCRIPTION = (
    "Требует сессию и `Origin` сервиса; `Origin` проверяется раньше сессии и разбора тела. "
    "Регистрирует репозиторий установки GitHub App с `auto_review_enabled = true`; `full_name` "
    "и `default_branch` — из данных установки. Проверки по порядку: провайдер не `github` или "
    "репозитория нет среди репозиториев установок — `404 REPOSITORY_NOT_IN_INSTALLATION`; у "
    "пользователя нет роли admin или maintain на репозитории в GitHub — `403 REPOSITORY_MANAGE_REQUIRED`; "
    "репозиторий уже подключён — `409 REPOSITORY_ALREADY_CONNECTED`. Пользователь без прав "
    "получает `403` и не узнаёт, подключён ли репозиторий. Если GitHub не ответил или ответил "
    "ошибкой — `502 SCM_UNAVAILABLE`, репозиторий не записан."
)
# Настройки и статус rules.md — задача #113 (бэкенд-часть dmc-268-ui-t1#28): контракт первым,
# реализация отдельно.
_SETTINGS_DEFAULTS = (
    "У репозитория без сохранённых настроек — значения по умолчанию: auto_review_enabled = true, "
    "branch_filter = [] (все ветки), min_severity = critical."
)
_GET_SETTINGS_DESCRIPTION = (
    "Не реализовано: отвечает 501 NOT_IMPLEMENTED. Описана заранее, чтобы фронтенд генерировал типы "
    "(задача #113). Требует сессию; id репозитория проверяется до ответа 501. Когда ручка заработает: "
    "настройки ревью репозитория; 404 REPOSITORY_NOT_FOUND, если репозитория нет. " + _SETTINGS_DEFAULTS
)
_SAVE_SETTINGS_DESCRIPTION = (
    "Не реализовано: отвечает 501 NOT_IMPLEMENTED. Описана заранее, чтобы фронтенд генерировал типы "
    "(задача #113). Требует сессию и `Origin` сервиса; `Origin` проверяется раньше сессии и разбора "
    "тела, id репозитория и тело — до ответа 501. Тело — полный набор настроек: все поля обязательны, "
    "запись заменяет прежние настройки целиком, повтор того же тела ничего не меняет. Когда ручка "
    "заработает: 200 с сохранёнными настройками; 404 REPOSITORY_NOT_FOUND, если репозитория нет; "
    "403 REPOSITORY_MANAGE_REQUIRED, если у пользователя нет роли admin или maintain на репозитории "
    "у провайдера, — как при подключении, роль проверяется запросом к GitHub."
)
_RULES_DESCRIPTION = (
    "Не реализовано: отвечает 501 NOT_IMPLEMENTED. Описана заранее, чтобы фронтенд генерировал типы "
    "(задача #113). Требует сессию; id репозитория проверяется до ответа 501. Когда ручка заработает: "
    "чем закончилась загрузка `.review/rules.md` с ветки по умолчанию репозитория — те же исходы, что "
    "у загрузки правил для прогона: loaded — файл разобран, в ответе принятые правила и отклонённые "
    "разделы; absent — файла нет; too_large — файл больше лимита и не разбирался; unavailable — "
    "провайдер не ответил. Кроме loaded, ревью идёт по стандартным критериям. 404 "
    "REPOSITORY_NOT_FOUND, если репозитория нет."
)
_SCM_UNAVAILABLE_DETAIL = "GitHub не ответил или ответил ошибкой."
# Отказ use case — ответ ручки.
_REFUSALS: dict[connecting.ConnectRefusal, tuple[HTTPStatus, ErrorCode, str]] = {
    connecting.ConnectRefusal.NOT_IN_INSTALLATION: (
        HTTPStatus.NOT_FOUND,
        ErrorCode.REPOSITORY_NOT_IN_INSTALLATION,
        "Репозитория нет среди репозиториев установок GitHub App.",
    ),
    connecting.ConnectRefusal.ALREADY_CONNECTED: (
        HTTPStatus.CONFLICT,
        ErrorCode.REPOSITORY_ALREADY_CONNECTED,
        "Репозиторий уже подключён.",
    ),
    connecting.ConnectRefusal.MANAGE_REQUIRED: (
        HTTPStatus.FORBIDDEN,
        ErrorCode.REPOSITORY_MANAGE_REQUIRED,
        "Подключать репозиторий может только пользователь с правами admin или maintain на него.",
    ),
}

# Сессия — на весь роутер: исключения видны здесь, а не в middleware.
router = APIRouter(
    prefix="/api/v1/repositories",
    tags=["repositories"],
    dependencies=SESSION_REQUIRED,
    responses=SESSION_RESPONSES,
)


class RepositoryResponse(RepositoryBase):
    """Репозиторий, зарегистрированный в сервисе: его запросы на изменение ревьюятся."""

    provider_id: str = Field(description="Идентификатор репозитория у провайдера.", examples=["123456789"])
    default_branch: str = Field(description="Ветка по умолчанию у провайдера.", examples=["main"])
    auto_review_enabled: bool = Field(description="Ревью запускается автоматически по вебхуку.")
    created_at: UtcDatetime = Field(description="Когда репозиторий зарегистрирован.")
    updated_at: UtcDatetime = Field(description="Когда запись последний раз менялась.")


class RepositoryListResponse(BaseModel):
    """Страница списка репозиториев."""

    items: list[RepositoryResponse] = Field(
        description="Репозитории страницы; меньше limit — значит, страница последняя."
    )


class AvailableRepositoryResponse(BaseModel):
    """Репозиторий у провайдера, который вошедший пользователь может подключить к сервису."""

    provider: Provider = Field(description="Хостинг репозитория.", examples=["github"])
    provider_id: str = Field(description="Идентификатор репозитория у провайдера.", examples=["123456789"])
    full_name: str = Field(description="Полное имя у провайдера: владелец/репозиторий.", examples=["octo-org/api"])
    default_branch: str = Field(description="Ветка по умолчанию у провайдера.", examples=["main"])
    already_connected: bool = Field(description="Репозиторий уже зарегистрирован в сервисе.")


class AvailableRepositoryListResponse(BaseModel):
    """Репозитории, доступные для подключения."""

    items: list[AvailableRepositoryResponse] = Field(description="Репозитории установки GitHub App.")


class ConnectRepositoryRequest(StrictRequest):
    """Запрос на подключение репозитория к сервису."""

    # strict у модели не пропустил бы enum строкой; для enum-поля — точечно, как в CreateReviewRequest.
    provider: Provider = Field(strict=False, description="Хостинг репозитория.", examples=["github"])
    provider_id: str = Field(
        min_length=1,
        max_length=255,
        description="Идентификатор репозитория у провайдера (из списка доступных).",
        examples=["123456789"],
    )


@router.get(
    "/available",
    response_model=AvailableRepositoryListResponse,
    summary="Репозитории, доступные для подключения",
    response_description="Репозитории установок GitHub App, к которым у пользователя есть доступ.",
    description=_AVAILABLE_DESCRIPTION,
    responses=ERRORS_502_SCM_REPOSITORIES,
)
def list_available_repositories(
    user: User = Depends(current_user),
    catalog: InstallationCatalog = Depends(get_installation_catalog),
    uow_factory: Callable[[], UnitOfWork] = Depends(get_unit_of_work_factory),
) -> AvailableRepositoryListResponse:
    try:
        available = connecting.list_available_repositories(catalog, uow_factory(), user.login)
    except VcsError as error:
        raise ApiError(HTTPStatus.BAD_GATEWAY, ErrorCode.SCM_UNAVAILABLE, _SCM_UNAVAILABLE_DETAIL) from error
    return AvailableRepositoryListResponse(
        items=[
            AvailableRepositoryResponse(
                provider=Provider.GITHUB,
                provider_id=item.repository.provider_id,
                full_name=item.repository.full_name,
                default_branch=item.repository.default_branch,
                already_connected=item.already_connected,
            )
            for item in available
        ]
    )


@router.post(
    "",
    status_code=HTTPStatus.CREATED,
    response_model=RepositoryResponse,
    summary="Подключить репозиторий",
    response_description="Репозиторий зарегистрирован.",
    description=_CONNECT_DESCRIPTION,
    responses={
        **ERRORS_403_ORIGIN_OR_MANAGE,
        **ERRORS_404_NOT_IN_INSTALLATION,
        **ERRORS_409_ALREADY_CONNECTED,
        **ERRORS_422,
        **ERRORS_502_SCM_REPOSITORIES,
    },
)
def connect_repository(
    body: ConnectRepositoryRequest,
    user: User = Depends(current_user),
    catalog: InstallationCatalog = Depends(get_installation_catalog),
    uow_factory: Callable[[], UnitOfWork] = Depends(get_unit_of_work_factory),
) -> RepositoryResponse:
    try:
        outcome = connecting.connect_repository(
            catalog, uow_factory(), body.provider, body.provider_id, user.login, new_id(), datetime.now(UTC)
        )
    except VcsError as error:
        raise ApiError(HTTPStatus.BAD_GATEWAY, ErrorCode.SCM_UNAVAILABLE, _SCM_UNAVAILABLE_DETAIL) from error
    if isinstance(outcome, connecting.ConnectRefusal):
        raise ApiError(*_REFUSALS[outcome])
    return RepositoryResponse.model_validate(outcome, from_attributes=True)


@router.get(
    "",
    response_model=RepositoryListResponse,
    summary="Список зарегистрированных репозиториев",
    response_description="Страница репозиториев.",
    description=(
        "Требует сессию. Постранично, по времени регистрации, затем по id. limit выше 200 не "
        "отклоняется, а приводится к 200. Общего числа записей в ответе нет: конец списка — "
        "страница короче limit."
    ),
    responses=ERRORS_422,
)
def list_repositories(
    limit: Limit = DEFAULT_LIMIT,
    offset: Offset = 0,
    uow: UnitOfWork = Depends(get_unit_of_work),
) -> RepositoryListResponse:
    repositories = uow.repositories.list_all(page_size(limit), offset)
    return RepositoryListResponse(
        items=[RepositoryResponse.model_validate(r, from_attributes=True) for r in repositories]
    )


# Ограничения фильтра веток в настройках.
BRANCH_PATTERN_MAX_LENGTH = 200
BRANCH_FILTER_MAX_ITEMS = 50

BranchPattern = Annotated[str, StringConstraints(min_length=1, max_length=BRANCH_PATTERN_MAX_LENGTH)]
RepositoryIdPath = Annotated[UUID, Path(description="Идентификатор репозитория в сервисе.")]

_AUTO_REVIEW_DESCRIPTION = (
    "Ревью запускается автоматически по вебхуку. Та же колонка репозитория, что auto_review_enabled "
    "в RepositoryResponse, — второй копии нет. Без сохранённых настроек — true."
)
_BRANCH_FILTER_DESCRIPTION = (
    "Glob-паттерны целевых веток запросов на изменение, которые ревьюятся; пустой список — все ветки "
    "(так и без сохранённых настроек)."
)
_MIN_SEVERITY_DESCRIPTION = (
    "Минимальная важность публикуемого замечания: менее важные не публикуются. Без сохранённых "
    "настроек — critical."
)


class RepositorySettingsResponse(BaseModel):
    """Настройки ревью репозитория."""

    auto_review_enabled: bool = Field(description=_AUTO_REVIEW_DESCRIPTION, examples=[True])
    branch_filter: list[str] = Field(description=_BRANCH_FILTER_DESCRIPTION, examples=[["main", "release/*"]])
    min_severity: FindingSeverity = Field(description=_MIN_SEVERITY_DESCRIPTION, examples=["critical"])


class RepositorySettingsUpdate(StrictRequest):
    """Новые настройки ревью репозитория — полный набор, заменяет прежний целиком."""

    auto_review_enabled: bool = Field(description=_AUTO_REVIEW_DESCRIPTION, examples=[True])
    branch_filter: list[BranchPattern] = Field(
        max_length=BRANCH_FILTER_MAX_ITEMS,
        description=(
            f"{_BRANCH_FILTER_DESCRIPTION} Не больше {BRANCH_FILTER_MAX_ITEMS} паттернов, каждый — "
            f"непустой, не длиннее {BRANCH_PATTERN_MAX_LENGTH} символов."
        ),
        examples=[["main", "release/*"]],
    )
    # strict у модели не пропустил бы enum строкой; для enum-поля — точечно, как в ConnectRepositoryRequest.
    min_severity: FindingSeverity = Field(strict=False, description=_MIN_SEVERITY_DESCRIPTION, examples=["critical"])


class RepoRule(BaseModel):
    """Правило владельца репозитория из `.review/rules.md`, принятое разбором."""

    name: str = Field(description="Имя правила — заголовок раздела.", examples=["Миграции"])
    patterns: list[str] = Field(description="Glob-паттерны файлов, к которым правило применяется.", examples=[["migrations/**"]])
    excludes: list[str] = Field(description="Glob-паттерны исключений; пустой список — не исключено ничего.")
    checks: list[str] = Field(description="Проверки правила — пункты раздела.")


class RejectedRule(BaseModel):
    """Раздел `.review/rules.md`, не ставший правилом."""

    name: str = Field(description="Заголовок раздела.")
    reason: str = Field(description="Чего разделу не хватило — по-русски, для показа пользователю.")


class _RulesFile(BaseModel):
    branch: str = Field(description="Ветка, с которой читался файл, — ветка по умолчанию репозитория.", examples=["main"])
    path: str = Field(description="Путь к файлу правил в репозитории.", examples=[RULES_PATH])


class RulesLoaded(_RulesFile):
    """Файл правил найден и разобран."""

    status: Literal[RulesStatus.LOADED]
    rules: list[RepoRule] = Field(description="Принятые правила по порядку в файле.")
    rejected: list[RejectedRule] = Field(description="Разделы, не ставшие правилами.")
    fetched_at: UtcDatetime = Field(description="Когда файл получен от провайдера.")


class RulesAbsent(_RulesFile):
    """Файла правил на ветке нет: ревью по стандартным критериям."""

    status: Literal[RulesStatus.ABSENT]


class RulesTooLarge(_RulesFile):
    """Файл правил больше лимита и не разбирался: ревью по стандартным критериям."""

    status: Literal[RulesStatus.TOO_LARGE]


class RulesUnavailable(_RulesFile):
    """Провайдер не отдал файл правил: ревью по стандартным критериям."""

    status: Literal[RulesStatus.UNAVAILABLE]


class RulesStatusResponse(
    RootModel[
        Annotated[RulesLoaded | RulesAbsent | RulesTooLarge | RulesUnavailable, Field(discriminator="status")]
    ]
):
    """Чем закончилась загрузка `.review/rules.md`; вариант — по `status`."""


@router.get(
    "/{repository_id}/settings",
    response_model=RepositorySettingsResponse,
    summary="Настройки ревью репозитория",
    response_description="Настройки репозитория.",
    description=_GET_SETTINGS_DESCRIPTION,
    responses={**ERRORS_404_REPOSITORY, **ERRORS_422, **ERRORS_501_NOT_IMPLEMENTED},
)
def get_repository_settings(repository_id: RepositoryIdPath) -> RepositorySettingsResponse:
    raise ApiError(HTTPStatus.NOT_IMPLEMENTED, ErrorCode.NOT_IMPLEMENTED, "Настройки репозитория ещё не реализованы.")


@router.put(
    "/{repository_id}/settings",
    response_model=RepositorySettingsResponse,
    summary="Сохранить настройки ревью репозитория",
    response_description="Настройки сохранены.",
    description=_SAVE_SETTINGS_DESCRIPTION,
    responses={
        **ERRORS_403_ORIGIN_OR_MANAGE,
        **ERRORS_404_REPOSITORY,
        **ERRORS_422,
        **ERRORS_501_NOT_IMPLEMENTED,
    },
)
def save_repository_settings(
    repository_id: RepositoryIdPath, body: RepositorySettingsUpdate
) -> RepositorySettingsResponse:
    raise ApiError(HTTPStatus.NOT_IMPLEMENTED, ErrorCode.NOT_IMPLEMENTED, "Настройки репозитория ещё не реализованы.")


@router.get(
    "/{repository_id}/rules",
    response_model=RulesStatusResponse,
    summary="Статус правил ревью репозитория",
    response_description="Исход загрузки `.review/rules.md`.",
    description=_RULES_DESCRIPTION,
    responses={**ERRORS_404_REPOSITORY, **ERRORS_422, **ERRORS_501_NOT_IMPLEMENTED},
)
def get_repository_rules(repository_id: RepositoryIdPath) -> RulesStatusResponse:
    raise ApiError(HTTPStatus.NOT_IMPLEMENTED, ErrorCode.NOT_IMPLEMENTED, "Статус правил репозитория ещё не реализован.")
