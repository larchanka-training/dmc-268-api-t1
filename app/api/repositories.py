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
from pydantic import BaseModel, Field, RootModel, StringConstraints, field_validator
from pydantic_core import PydanticCustomError

from app.api.dependencies import (
    get_installation_catalog,
    get_token_provider,
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
from app.api.session import (
    SESSION_REQUIRED,
    SESSION_RESPONSES,
    Unauthenticated,
    current_session,
)
from app.application.ports import InstallationCatalog, OAuthProvider, UnitOfWork

# Под своими именами: имена функций роутов — operationId в схеме, их держат типы фронтенда.
from app.application.use_cases import connect_repository as connecting
from app.application.use_cases.auth import SignedIn
from app.application.use_cases.user_token import SessionClosed
from app.domain.enums import FindingSeverity, Provider, RulesStatus
from app.domain.ids import new_id
from app.domain.login import OAuthProviderUnavailable
from app.domain.repo_rules import RULES_PATH
from app.domain.vcs_errors import VcsError

# Форма — вариант A решения 3 в dmc-268-ui-t1#37; поведение — change `repositories-via-user-token`.
_AVAILABLE_DESCRIPTION = (
    "Требует сессию. Репозитории установок GitHub App, доступные вошедшему пользователю, с "
    "отметкой `already_connected` — зарегистрирован ли репозиторий в сервисе. Список отдаёт "
    "GitHub по токену пользователя, сохранённому при входе (`GET /user/installations` и "
    "`GET /user/installations/{id}/repositories`): это пересечение «доступно приложению» и "
    "«доступно пользователю», остальные репозитории установок в ответ не попадают, это не "
    "ошибка. Публичный репозиторий, на котором у пользователя нет роли, тоже не попадает. "
    "Подключить из списка можно только с правом admin или maintain: с push или triage "
    "подключение ответит `403 REPOSITORY_MANAGE_REQUIRED`, только с правом чтения — "
    "`404 REPOSITORY_NOT_IN_INSTALLATION`. Токен пользователя отозван, истёк без "
    "возможности обновления или его нет (сессия открыта до хранения токенов) — "
    "`401 UNAUTHENTICATED`, сессия закрывается, cookie удаляется: нужен новый вход. Если "
    "GitHub не ответил или ответил ошибкой — `502 SCM_UNAVAILABLE`."
)
_CONNECT_DESCRIPTION = (
    "Требует сессию и `Origin` сервиса; `Origin` проверяется раньше сессии и разбора тела. "
    "Регистрирует репозиторий установки GitHub App с `auto_review_enabled = true`; `full_name` "
    "и `default_branch` — из ответа GitHub. Два запроса к GitHub: репозиторий по id токеном "
    "пользователя (`GET /repositories/{id}`), затем установка приложения на нём "
    "(`GET /repos/{owner}/{repo}/installation`). Проверки по порядку: провайдер не `github`, "
    "репозиторий не виден пользователю или виден только с правом чтения (так GitHub отдаёт "
    "любой публичный репозиторий), приложение на нём не установлено или установка "
    "приостановлена — `404 REPOSITORY_NOT_IN_INSTALLATION`, один ответ без различия; у "
    "пользователя нет права admin или "
    "maintain на репозиторий в GitHub — `403 REPOSITORY_MANAGE_REQUIRED`; репозиторий уже "
    "подключён — `409 REPOSITORY_ALREADY_CONNECTED`. Пользователь без прав получает `403` и не "
    "узнаёт, подключён ли репозиторий. Токен пользователя непригоден — `401 UNAUTHENTICATED`, "
    "сессия закрывается. Если GitHub не ответил или ответил ошибкой — `502 SCM_UNAVAILABLE`, "
    "репозиторий не записан."
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
        "Репозитория нет среди репозиториев установок GitHub App, доступных пользователю.",
    ),
    connecting.ConnectRefusal.ALREADY_CONNECTED: (
        HTTPStatus.CONFLICT,
        ErrorCode.REPOSITORY_ALREADY_CONNECTED,
        "Репозиторий уже подключён.",
    ),
    connecting.ConnectRefusal.MANAGE_REQUIRED: (
        HTTPStatus.FORBIDDEN,
        ErrorCode.REPOSITORY_MANAGE_REQUIRED,
        "Подключать репозиторий может только пользователь с правом admin или maintain на него.",
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

    items: list[AvailableRepositoryResponse] = Field(description="Репозитории установок GitHub App, доступные пользователю.")


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


def available_repositories(
    signed_in: SignedIn = Depends(current_session),
    catalog: InstallationCatalog = Depends(get_installation_catalog),
    oauth: OAuthProvider = Depends(get_token_provider),
    uow_factory: Callable[[], UnitOfWork] = Depends(get_unit_of_work_factory),
) -> list[connecting.AvailableRepository]:
    """Репозитории, видимые пользователю у провайдера по его токену, или ошибка.

    Токен непригоден — сессия закрыта, `401 UNAUTHENTICATED`; GitHub недоступен —
    `502 SCM_UNAVAILABLE`. Зависимость, а не код ручки: тот же список задаёт
    видимость прогонов в потоке GET /events (`app/api/events.py`). Правило одно —
    поменяется здесь, поменяется у обоих.
    """
    user = connecting.UserContext(oauth=oauth, session_id=signed_in.session_id)
    try:
        return connecting.list_available_repositories(catalog, uow_factory(), user, datetime.now(UTC))
    except SessionClosed:
        raise Unauthenticated(had_cookie=True) from None
    except (VcsError, OAuthProviderUnavailable) as error:
        raise ApiError(HTTPStatus.BAD_GATEWAY, ErrorCode.SCM_UNAVAILABLE, _SCM_UNAVAILABLE_DETAIL) from error


@router.get(
    "/available",
    response_model=AvailableRepositoryListResponse,
    summary="Репозитории, доступные для подключения",
    response_description="Репозитории установок GitHub App, доступные пользователю.",
    description=_AVAILABLE_DESCRIPTION,
    responses=ERRORS_502_SCM_REPOSITORIES,
)
def list_available_repositories(
    available: list[connecting.AvailableRepository] = Depends(available_repositories),
) -> AvailableRepositoryListResponse:
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
    signed_in: SignedIn = Depends(current_session),
    catalog: InstallationCatalog = Depends(get_installation_catalog),
    oauth: OAuthProvider = Depends(get_token_provider),
    uow_factory: Callable[[], UnitOfWork] = Depends(get_unit_of_work_factory),
) -> RepositoryResponse:
    user = connecting.UserContext(oauth=oauth, session_id=signed_in.session_id)
    try:
        outcome = connecting.connect_repository(
            catalog, uow_factory(), user, body.provider, body.provider_id, new_id(), datetime.now(UTC)
        )
    except SessionClosed:
        raise Unauthenticated(had_cookie=True) from None
    except (VcsError, OAuthProviderUnavailable) as error:
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

# Пробелы по краям отрезаются: паттерн из одних пробелов — пустой и отклоняется.
BranchPattern = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=BRANCH_PATTERN_MAX_LENGTH)
]
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
    "Порог важности: нерешённое замечание с severity не ниже порога блокирует merge gate, и бот "
    "запрашивает изменения. Публикуются все замечания, порог их не отсекает. Без сохранённых "
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
            f"непустой после обрезки пробелов, не длиннее {BRANCH_PATTERN_MAX_LENGTH} символов, без повторов."
        ),
        examples=[["main", "release/*"]],
    )
    # strict у модели не пропустил бы enum строкой; для enum-поля — точечно, как в ConnectRepositoryRequest.
    min_severity: FindingSeverity = Field(strict=False, description=_MIN_SEVERITY_DESCRIPTION, examples=["critical"])

    @field_validator("branch_filter")
    @classmethod
    def _patterns_are_unique(cls, patterns: list[str]) -> list[str]:
        """Повтор паттерна — ошибка, а не молчаливая склейка: форма должна показать, что не так."""
        if len(set(patterns)) != len(patterns):
            raise PydanticCustomError("duplicate_patterns", "паттерны веток повторяются")
        return patterns


class RepoRule(BaseModel):
    """Правило владельца репозитория из `.review/rules.md`, принятое разбором."""

    name: str = Field(description="Имя правила — заголовок раздела.", examples=["Миграции"])
    patterns: list[str] = Field(description="Glob-паттерны файлов, к которым правило применяется.", examples=[["migrations/**"]])
    excludes: list[str] = Field(
        description="Glob-паттерны исключений; пустой список — не исключено ничего.", examples=[["**/tests/**"]]
    )
    checks: list[str] = Field(
        description="Проверки правила — пункты раздела.", examples=[["Миграция обратима: есть downgrade"]]
    )


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
    "/{repository_id}",
    response_model=RepositoryResponse,
    summary="Зарегистрированный репозиторий",
    response_description="Репозиторий.",
    description=(
        "Требует сессию. Один зарегистрированный репозиторий по id — та же форма, что строка "
        "GET /repositories. 404 REPOSITORY_NOT_FOUND, если репозитория нет."
    ),
    responses={**ERRORS_404_REPOSITORY, **ERRORS_422},
)
def get_repository(
    repository_id: RepositoryIdPath, uow: UnitOfWork = Depends(get_unit_of_work)
) -> RepositoryResponse:
    repository = uow.repositories.get(repository_id)
    if repository is None:
        raise ApiError(HTTPStatus.NOT_FOUND, ErrorCode.REPOSITORY_NOT_FOUND, "Репозитория с таким id нет.")
    return RepositoryResponse.model_validate(repository, from_attributes=True)


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
