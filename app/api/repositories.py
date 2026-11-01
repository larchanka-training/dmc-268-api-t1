"""Репозитории: список видимых пользователю, чтение, подключение, выключение, настройки ревью и
статус `.review/rules.md`.

Транспорт: разбор запроса, вызов порта или use case, формат ответа. Пагинация —
`app/api/pagination.py`. Правила подключения — `app/application/use_cases/connect_repository.py`.
"""

from collections.abc import Callable, Collection
from dataclasses import replace
from datetime import UTC, datetime
from http import HTTPStatus
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Request, Response
from pydantic import (
    AfterValidator,
    BaseModel,
    Field,
    RootModel,
    StringConstraints,
    field_validator,
)
from pydantic_core import PydanticCustomError

from app.api.dependencies import (
    get_app_slug,
    get_cabinet_vcs_gateway,
    get_installation_catalog,
    get_rules_cache,
    get_settings,
    get_token_provider,
    get_unit_of_work_factory,
    get_visibility_catalog,
)
from app.api.errors import (
    ERRORS_403_ORIGIN_OR_MANAGE,
    ERRORS_404_NOT_IN_INSTALLATION,
    ERRORS_404_REPOSITORY,
    ERRORS_404_REPOSITORY_OR_NOT_IN_INSTALLATION,
    ERRORS_409_ALREADY_CONNECTED,
    ERRORS_413_BODY,
    ERRORS_422,
    ERRORS_502_SCM_REPOSITORIES,
    ERRORS_502_SCM_VISIBILITY,
    ApiError,
)
from app.api.pagination import DEFAULT_LIMIT, Limit, Offset, page_size
from app.api.problems import ErrorCode
from app.api.schemas import RepositoryBase, StrictRequest, UtcDatetime, without_nul
from app.api.session import (
    SESSION_COOKIE,
    SESSION_REQUIRED,
    SESSION_RESPONSES,
    Unauthenticated,
    current_session,
)
from app.application.ports import InstallationCatalog, OAuthProvider, UnitOfWork
from app.application.ports.cache_store import CacheStore
from app.application.ports.vcs_gateway import VcsGateway

# Под своими именами: имена функций роутов — operationId в схеме, их держат типы фронтенда.
from app.application.use_cases import connect_repository as connecting
from app.application.use_cases.auth import SignedIn
from app.application.use_cases.repository_rules import load_default_branch_rules
from app.application.use_cases.run_diff import RepositoryNotInstalled
from app.application.use_cases.user_token import SessionClosed
from app.config import Settings
from app.domain.entities import (
    InstallationRepository,
    RepoRules,
    Repository,
    RepositorySettings,
)
from app.domain.enums import FindingSeverity, Provider, RulesStatus
from app.domain.ids import new_id
from app.domain.login import OAuthProviderUnavailable
from app.domain.provider_links import app_install_url
from app.domain.repo_rules import RULES_PATH
from app.domain.session import setup_state
from app.domain.vcs_errors import VcsError

# Один список вместо двух — change `repositories-one-list` (#243, dmc-268-ui-t1#95).
_LIST_DESCRIPTION = (
    "Требует сессию. Все репозитории установок GitHub App, которые GitHub отдаёт пользователю по "
    "его токену, сохранённому при входе, подключённые и нет; публичный репозиторий, на котором у "
    "пользователя нет роли, в ответ не попадает. Ответ GitHub — из кэша видимости (по умолчанию "
    "30 с, как у GET /reviews и GET /events), подключён ли репозиторий — из базы. Постранично, "
    "по `full_name` без учёта регистра; limit выше 200 приводится к 200, конец списка — страница "
    "короче limit. Токен пользователя непригоден — `401 UNAUTHENTICATED`, сессия закрывается; "
    "GitHub не ответил или ответил ошибкой — `502 SCM_UNAVAILABLE`."
)
_CONNECT_DESCRIPTION = (
    "Требует сессию и `Origin` сервиса; `Origin` проверяется раньше сессии и разбора тела. "
    "Регистрирует репозиторий установки GitHub App; новый получает настройки по умолчанию, в том "
    "числе `auto_review_enabled = true`, а включённый обратно после `DELETE` сохраняет свои "
    "сохранённые настройки, в том числе `auto_review_enabled = false`. `full_name` "
    "и `default_branch` — из ответа GitHub. Два запроса к GitHub: репозиторий по id токеном "
    "пользователя (`GET /repositories/{id}`), затем установка приложения на нём "
    "(`GET /repos/{owner}/{repo}/installation`). Проверки по порядку: провайдер не `github`, "
    "репозиторий не виден пользователю или публичный виден только с правом чтения (так GitHub "
    "отдаёт любой публичный репозиторий), приложение на нём не установлено или установка "
    "приостановлена — `404 REPOSITORY_NOT_IN_INSTALLATION`, один ответ без различия; у "
    "пользователя нет права admin или "
    "maintain на репозиторий в GitHub, в том числе роль read на приватном — "
    "`403 REPOSITORY_MANAGE_REQUIRED`; репозиторий уже "
    "подключён и включён — `409 REPOSITORY_ALREADY_CONNECTED`; интерфейс считает его успехом: "
    "ревью уже включено, в том числе параллельным запросом. Выключенный `DELETE` репозиторий "
    "включается обратно — `201` с прежним `id`, история прогонов сохраняется. После повторного включения head, запушенный в выключенный период, остаётся с проверкой «Ревью выключено» до следующего push. Пользователь без "
    "прав получает `403` и не узнаёт, подключён ли репозиторий. Токен пользователя непригоден — `401 UNAUTHENTICATED`, "
    "сессия закрывается. Если GitHub не ответил или ответил ошибкой — `502 SCM_UNAVAILABLE`, "
    "репозиторий не записан."
)
_DISCONNECT_DESCRIPTION = (
    "Требует сессию и `Origin` сервиса; `Origin` проверяется раньше сессии. Выключает ревью мягко: "
    "запись, запросы на изменение и история прогонов остаются; на вебхук `pull_request` прогона нет, "
    "а на head ставится проверка `success` «Ревью выключено в сервисе»; на вебхук "
    "`pull_request_review_thread` тред записывается, а на head ставится та же проверка. Повторный "
    "POST /repositories включает обратно; уже выключен — тоже `204`. Прогон, поставленный в "
    "очередь до выключения, доводится до конца. Уже стоящую проверку выключение не меняет: открытый "
    "PR с `failure` разблокирует push или закрытие треда. Проверки по порядку: репозитория нет или "
    "пользователь не видит его на GitHub (кэш видимости, как у GET /repositories/{id}) — "
    "`404 REPOSITORY_NOT_FOUND`, ответ один и тот же; права — как у POST /repositories, запросом к "
    "GitHub мимо кэша: без admin или maintain — `403 REPOSITORY_MANAGE_REQUIRED`. Токен "
    "пользователя непригоден — `401 UNAUTHENTICATED`, сессия закрывается; GitHub не ответил или "
    "ответил ошибкой — `502 SCM_UNAVAILABLE`, ревью не выключено."
)
_SETTINGS_DEFAULTS = (
    "У репозитория без сохранённых настроек — значения по умолчанию: auto_review_enabled = true, "
    "branch_filter = [] (все ветки), min_severity = critical."
)
_VISIBILITY_REFUSALS = (
    "404 REPOSITORY_NOT_FOUND, если репозитория нет или пользователь не видит его на GitHub (кэш "
    "видимости, как у GET /repositories/{id}), — ответ один и тот же. Токен пользователя непригоден — "
    "`401 UNAUTHENTICATED`, сессия закрывается; GitHub не ответил или ответил ошибкой — "
    "`502 SCM_UNAVAILABLE`."
)
_GET_SETTINGS_DESCRIPTION = (
    "Требует сессию. Настройки ревью видимого пользователю репозитория, в том числе выключенного "
    "`DELETE`. " + _SETTINGS_DEFAULTS + " " + _VISIBILITY_REFUSALS
)
_SAVE_SETTINGS_DESCRIPTION = (
    "Требует сессию и `Origin` сервиса; `Origin` проверяется раньше сессии и разбора тела, id "
    "репозитория и тело — раньше обращения к базе и к GitHub. Тело — полный набор настроек: все поля "
    "обязательны, запись заменяет прежние настройки целиком, повтор того же тела ничего не меняет; "
    "паттерны веток хранятся обрезанными. Ответ — 200 с настройками, какими их записала база. "
    "Проверки по "
    "порядку: видимость — " + _VISIBILITY_REFUSALS + " Затем роль — как у POST и DELETE "
    "/repositories, запросом к GitHub мимо кэша видимости: без admin или maintain — "
    "`403 REPOSITORY_MANAGE_REQUIRED`, GitHub не отдал репозиторий — `404 REPOSITORY_NOT_FOUND`; "
    "при отказе и при `502` настройки не меняются. Выключенный репозиторий настройки получает, но не "
    "включается — включение только POST /repositories. Смена порога уже стоящую проверку merge "
    "gate не пересчитывает: новый порог применится со следующим прогоном или вебхуком закрытия треда."
)
_RULES_DESCRIPTION = (
    "Требует сессию. Чем закончилась загрузка `.review/rules.md` с последнего коммита ветки по "
    "умолчанию репозитория — те же лимит, разбор и исходы, что у загрузки правил для прогона: "
    "loaded — файл разобран, в ответе принятые правила и отклонённые разделы; empty — файл "
    "разобран, но ни один раздел не стал правилом, в ответе отклонённые разделы; absent — файла "
    "нет; too_large — файл больше лимита и не разбирался; unavailable — GitHub не ответил на запрос "
    "установки приложения, на разрешение ветки в коммит или на чтение файла (это ответ `200`, а не "
    "ошибка). Кроме loaded, ревью идёт по стандартным критериям. Ветка разрешается в коммит на "
    "каждый запрос — текущая ветка по умолчанию из ответа GitHub, как в GET /repositories; исход "
    "файла кэшируется по паре «репозиторий, коммит» (свой кэш процесса API, тот же ключ и TTL, что "
    "у прогона): "
    "на том же коммите файл повторно не запрашивается и `fetched_at` — время первого получения; "
    "сбой не кэшируется. " + _VISIBILITY_REFUSALS + " Приложение не установлено на репозиторий или "
    "установка приостановлена — `404 REPOSITORY_NOT_IN_INSTALLATION`, ветка и файл не запрашиваются."
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
    """Репозиторий, зарегистрированный в сервисе, в том числе выключенный: его запросы на изменение ревьюятся,
    пока ревью включено."""

    provider_id: str = Field(description="Идентификатор репозитория у провайдера.", examples=["123456789"])
    default_branch: str = Field(description="Ветка по умолчанию у провайдера.", examples=["main"])
    auto_review_enabled: bool = Field(description="Ревью запускается автоматически по вебхуку.")
    created_at: UtcDatetime = Field(description="Когда репозиторий зарегистрирован.")
    updated_at: UtcDatetime = Field(description="Когда запись последний раз менялась.")


class RepositoryListItem(BaseModel):
    """Репозиторий установки GitHub App, видимый пользователю: подключённый или нет.

    Подключённый — ревью включено, его запросы на изменение ревьюятся; неподключённый можно
    подключить, если `can_connect`.
    """

    provider: Provider = Field(description="Хостинг репозитория.", examples=["github"])
    provider_id: str = Field(description="Идентификатор репозитория у провайдера.", examples=["123456789"])
    full_name: str = Field(
        description="Полное имя у провайдера: владелец/репозиторий, из ответа GitHub.", examples=["octo-org/api"]
    )
    default_branch: str = Field(description="Ветка по умолчанию у провайдера, из ответа GitHub.", examples=["main"])
    connected: bool = Field(description="Ревью включено: репозиторий подключён к сервису и не выключен.")
    id: UUID | None = Field(
        description=(
            "Идентификатор репозитория в сервисе — для настроек, прогонов и DELETE /repositories/{id}. "
            "Есть и у выключенного: история остаётся; `null`, если репозиторий не подключали."
        )
    )
    can_connect: bool = Field(
        description=(
            "У пользователя на репозитории роль admin или maintain — то же правило, что у POST и DELETE "
            "/repositories. Подсказка интерфейсу: без неё переключатель «Ревью» неактивен; ручки проверяют "
            "права сами."
        )
    )
    connected_at: UtcDatetime | None = Field(
        description="Когда ревью включено последний раз; `null`, если ревью не включено."
    )


class RepositoryListResponse(BaseModel):
    """Страница списка репозиториев и адрес установки приложения."""

    items: list[RepositoryListItem] = Field(
        description="Репозитории страницы; меньше limit — значит, страница последняя."
    )
    install_url: str | None = Field(
        description=(
            "Экран установки GitHub App (`https://github.com/apps/<slug>/installations/new?state=<state>`): "
            "выбрать аккаунт и репозитории. `state` привязан к сессии, GitHub возвращает его на Setup URL "
            "(GET /auth/github/setup): без него возврат ничего не подключает. `null`, если slug приложения "
            "узнать не удалось: нет ключа приложения или GitHub не ответил на `GET /app`."
        ),
        json_schema_extra={"format": "uri"},
        examples=["https://github.com/apps/dmc-268-review/installations/new?state=3f2a9c0e"],
    )


class ConnectRepositoryRequest(StrictRequest):
    """Запрос на подключение репозитория к сервису."""

    # strict у модели не пропустил бы enum строкой; для enum-поля — точечно, как в CreateReviewRequest.
    provider: Provider = Field(strict=False, description="Хостинг репозитория.", examples=["github"])
    # Канонический id: GitHub отдаёт на `/repositories/0101` тот же репозиторий, что на `/101`.
    provider_id: str = Field(
        min_length=1,
        max_length=255,
        pattern=r"^[1-9][0-9]*$",
        description="Идентификатор репозитория у провайдера (из списка репозиториев): число без ведущих нулей.",
        examples=["123456789"],
    )


def _as_user[T](signed_in: SignedIn, oauth: OAuthProvider, call: Callable[[connecting.UserContext], T]) -> T:
    """Вызов провайдера токеном пользователя с переводом сбоев в ответы API.

    Токен непригоден — сессия закрыта, `401 UNAUTHENTICATED`; GitHub недоступен —
    `502 SCM_UNAVAILABLE`.
    """
    user = connecting.UserContext(oauth=oauth, session_id=signed_in.session_id)
    try:
        return call(user)
    except SessionClosed:
        raise Unauthenticated(had_cookie=True) from None
    except (VcsError, OAuthProviderUnavailable) as error:
        raise ApiError(HTTPStatus.BAD_GATEWAY, ErrorCode.SCM_UNAVAILABLE, _SCM_UNAVAILABLE_DETAIL) from error


def user_repositories(
    signed_in: SignedIn = Depends(current_session),
    catalog: InstallationCatalog = Depends(get_visibility_catalog),
    oauth: OAuthProvider = Depends(get_token_provider),
    uow_factory: Callable[[], UnitOfWork] = Depends(get_unit_of_work_factory),
) -> list[InstallationRepository]:
    """Репозитории, видимые пользователю на GitHub, без отметки подключённых, — видимость
    прогонов и репозиториев.

    Тот же ответ GitHub, что строки GET /repositories, из кэша в пределах TTL. Правило
    видимости одно — поменяется здесь, поменяется у всех.
    """
    now = datetime.now(UTC)
    return _as_user(signed_in, oauth, lambda user: connecting.list_user_repositories(catalog, uow_factory(), user, now))


def is_visible(repository: Repository, provider_ids: Collection[str]) -> bool:
    """Правило видимости кабинета: репозиторий виден, если GitHub отдаёт его пользователю.

    Кабинет видит репозитории только через GitHub: репозиторий другого провайдера не виден никому.
    """
    return repository.provider is Provider.GITHUB and repository.provider_id in provider_ids


def visible_repository(
    repository_id: UUID,
    signed_in: SignedIn,
    catalog: InstallationCatalog,
    oauth: OAuthProvider,
    uow_factory: Callable[[], UnitOfWork],
) -> Repository:
    """Репозиторий, если он виден пользователю; невидимый отвечает тем же `404`, что несуществующий.

    GitHub спрашиваем до чтения базы и при любом id, как у прогона: иначе `502`, `401` или
    время ответа выдали бы, что такой id есть. Вызывается из тела ручки, а не зависимостью:
    зависимость выполнилась бы и при `422` других параметров — запрос к GitHub впустую.
    """
    return _visible_with_host(repository_id, signed_in, catalog, oauth, uow_factory)[0]


def _visible_with_host(
    repository_id: UUID,
    signed_in: SignedIn,
    catalog: InstallationCatalog,
    oauth: OAuthProvider,
    uow_factory: Callable[[], UnitOfWork],
) -> tuple[Repository, InstallationRepository]:
    """`visible_repository` и его строка из ответа GitHub — со свежими `full_name` и `default_branch`.

    В базе они те, что были при подключении: вебхука `repository` у приложения нет.
    """
    visible = {r.provider_id: r for r in user_repositories(signed_in, catalog, oauth, uow_factory)}
    with uow_factory() as uow:
        repository = uow.repositories.get(repository_id)
    if repository is None or not is_visible(repository, visible):
        raise _repository_not_found()
    return repository, visible[repository.provider_id]


def _repository_not_found() -> ApiError:
    return ApiError(HTTPStatus.NOT_FOUND, ErrorCode.REPOSITORY_NOT_FOUND, "Репозитория с таким id нет.")


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
        **ERRORS_413_BODY,
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
    outcome = _as_user(
        signed_in,
        oauth,
        lambda user: connecting.connect_repository(
            catalog, uow_factory(), user, body.provider, body.provider_id, new_id(), datetime.now(UTC)
        ),
    )
    if isinstance(outcome, connecting.ConnectRefusal):
        raise ApiError(*_REFUSALS[outcome])
    return RepositoryResponse.model_validate(outcome, from_attributes=True)


@router.get(
    "",
    response_model=RepositoryListResponse,
    summary="Список репозиториев",
    response_description="Страница репозиториев и адрес установки приложения.",
    description=_LIST_DESCRIPTION,
    responses={**ERRORS_422, **ERRORS_502_SCM_VISIBILITY},
)
def list_repositories(
    request: Request,
    limit: Limit = DEFAULT_LIMIT,
    offset: Offset = 0,
    signed_in: SignedIn = Depends(current_session),
    catalog: InstallationCatalog = Depends(get_visibility_catalog),
    oauth: OAuthProvider = Depends(get_token_provider),
    uow_factory: Callable[[], UnitOfWork] = Depends(get_unit_of_work_factory),
    app_slug: Callable[[], str | None] = Depends(get_app_slug),
) -> RepositoryListResponse:
    # GitHub — в теле, а не зависимостью: при 422 limit или offset запроса к нему нет.
    now = datetime.now(UTC)
    listed = _as_user(
        signed_in, oauth, lambda user: connecting.list_with_connections(catalog, uow_factory(), user, now)
    )
    # Строк не больше, чем отдаёт GitHub пользователю: страница — срез в памяти.
    page = listed[offset : offset + page_size(limit)]
    slug = app_slug()
    # Cookie проверена `current_session`; `state` — от неё, Setup URL сверит его с той же cookie.
    state = setup_state(request.cookies[SESSION_COOKIE])
    install_url = app_install_url(slug, state) if slug else None
    return RepositoryListResponse(items=[_list_item(item) for item in page], install_url=install_url)


def _list_item(item: connecting.ListedRepository) -> RepositoryListItem:
    record = item.record
    # `id` — если запись есть, в том числе у выключенного; включён ли — по `connected_at` (design D5).
    connected_at = record.connected_at if record is not None else None
    return RepositoryListItem(
        provider=Provider.GITHUB,
        provider_id=item.repository.provider_id,
        full_name=item.repository.full_name,
        default_branch=item.repository.default_branch,
        connected=connected_at is not None,
        id=record.id if record is not None else None,
        can_connect=item.can_connect,
        connected_at=connected_at,
    )


# Ограничения фильтра веток в настройках.
BRANCH_PATTERN_MAX_LENGTH = 200
BRANCH_FILTER_MAX_ITEMS = 50

# Пробелы по краям отрезаются: паттерн из одних пробелов — пустой и отклоняется. NUL — `422`:
# в `text[]` PostgreSQL его не примет, и без проверки вышел бы `500`.
BranchPattern = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=BRANCH_PATTERN_MAX_LENGTH),
    AfterValidator(without_nul),
]
RepositoryIdPath = Annotated[UUID, Path(description="Идентификатор репозитория в сервисе.")]

_AUTO_REVIEW_DESCRIPTION = (
    "Ревью запускается автоматически по вебхуку. Та же колонка репозитория, что auto_review_enabled "
    "в RepositoryResponse, — второй копии нет. Без сохранённых настроек — true."
)
_BRANCH_FILTER_DESCRIPTION = (
    "Glob-паттерны целевых веток запросов на изменение, которые ревьюятся; пустой список — все ветки "
    "(так и без сохранённых настроек). Пока не применяется: сохраняется, но ревью идёт для всех "
    "веток (задача #313)."
)
_MIN_SEVERITY_DESCRIPTION = (
    "Порог важности: нерешённое замечание с severity не ниже порога блокирует merge gate: check "
    "run проверки — failure. Публикуются все замечания, порог их не отсекает. Без сохранённых "
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


class RulesEmpty(_RulesFile):
    """Файл правил разобран, но ни один раздел не стал правилом: ревью по стандартным критериям."""

    status: Literal[RulesStatus.EMPTY]
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
        Annotated[
            RulesLoaded | RulesEmpty | RulesAbsent | RulesTooLarge | RulesUnavailable,
            Field(discriminator="status"),
        ]
    ]
):
    """Чем закончилась загрузка `.review/rules.md`; вариант — по `status`."""


@router.get(
    "/{repository_id}",
    response_model=RepositoryResponse,
    summary="Зарегистрированный репозиторий",
    response_description="Репозиторий.",
    description=(
        "Требует сессию. Один зарегистрированный репозиторий по id — тот же `id`, что у строки "
        "GET /repositories. 404 REPOSITORY_NOT_FOUND, если репозитория нет или пользователь не "
        "видит его на GitHub — ответ один и тот же."
    ),
    responses={**ERRORS_404_REPOSITORY, **ERRORS_422, **ERRORS_502_SCM_VISIBILITY},
)
def get_repository(
    repository_id: RepositoryIdPath,
    signed_in: SignedIn = Depends(current_session),
    catalog: InstallationCatalog = Depends(get_visibility_catalog),
    oauth: OAuthProvider = Depends(get_token_provider),
    uow_factory: Callable[[], UnitOfWork] = Depends(get_unit_of_work_factory),
) -> RepositoryResponse:
    repository = visible_repository(repository_id, signed_in, catalog, oauth, uow_factory)
    return RepositoryResponse.model_validate(repository, from_attributes=True)


@router.delete(
    "/{repository_id}",
    status_code=HTTPStatus.NO_CONTENT,
    response_class=Response,
    summary="Выключить ревью репозитория",
    description=_DISCONNECT_DESCRIPTION,
    responses={
        HTTPStatus.NO_CONTENT: {"description": "Ревью выключено или уже было выключено."},
        **ERRORS_403_ORIGIN_OR_MANAGE,
        **ERRORS_404_REPOSITORY,
        **ERRORS_422,
        **ERRORS_502_SCM_VISIBILITY,
    },
)
def disconnect_repository(
    repository_id: RepositoryIdPath,
    signed_in: SignedIn = Depends(current_session),
    catalog: InstallationCatalog = Depends(get_visibility_catalog),
    oauth: OAuthProvider = Depends(get_token_provider),
    uow_factory: Callable[[], UnitOfWork] = Depends(get_unit_of_work_factory),
) -> Response:
    repository = visible_repository(repository_id, signed_in, catalog, oauth, uow_factory)
    # Права — мимо кэша: `user_repository` кэш видимости не читает.
    now = datetime.now(UTC)
    refusal = _as_user(
        signed_in, oauth, lambda user: connecting.disconnect_repository(catalog, uow_factory(), user, repository, now)
    )
    if refusal is connecting.ConnectRefusal.NOT_IN_INSTALLATION:
        raise _repository_not_found()
    if refusal is connecting.ConnectRefusal.MANAGE_REQUIRED:
        raise ApiError(
            HTTPStatus.FORBIDDEN,
            ErrorCode.REPOSITORY_MANAGE_REQUIRED,
            "Выключать ревью может только пользователь с правом admin или maintain на репозиторий.",
        )
    return Response(status_code=HTTPStatus.NO_CONTENT)


def _settings_response(settings: Repository | RepositorySettings) -> RepositorySettingsResponse:
    """Ответ из настроек: у `Repository` и `RepositorySettings` те же три поля."""
    return RepositorySettingsResponse(
        auto_review_enabled=settings.auto_review_enabled,
        branch_filter=list(settings.branch_filter),
        min_severity=settings.min_severity,
    )


@router.get(
    "/{repository_id}/settings",
    response_model=RepositorySettingsResponse,
    summary="Настройки ревью репозитория",
    response_description="Настройки репозитория.",
    description=_GET_SETTINGS_DESCRIPTION,
    responses={**ERRORS_404_REPOSITORY, **ERRORS_422, **ERRORS_502_SCM_VISIBILITY},
)
def get_repository_settings(
    repository_id: RepositoryIdPath,
    signed_in: SignedIn = Depends(current_session),
    catalog: InstallationCatalog = Depends(get_visibility_catalog),
    oauth: OAuthProvider = Depends(get_token_provider),
    uow_factory: Callable[[], UnitOfWork] = Depends(get_unit_of_work_factory),
) -> RepositorySettingsResponse:
    # Настройки — колонки той же записи, что вернула проверка видимости: второго запроса нет.
    return _settings_response(visible_repository(repository_id, signed_in, catalog, oauth, uow_factory))


@router.put(
    "/{repository_id}/settings",
    response_model=RepositorySettingsResponse,
    summary="Сохранить настройки ревью репозитория",
    response_description="Настройки сохранены.",
    description=_SAVE_SETTINGS_DESCRIPTION,
    responses={
        **ERRORS_403_ORIGIN_OR_MANAGE,
        **ERRORS_404_REPOSITORY,
        **ERRORS_413_BODY,
        **ERRORS_422,
        **ERRORS_502_SCM_VISIBILITY,
    },
)
def save_repository_settings(
    repository_id: RepositoryIdPath,
    body: RepositorySettingsUpdate,
    signed_in: SignedIn = Depends(current_session),
    catalog: InstallationCatalog = Depends(get_visibility_catalog),
    oauth: OAuthProvider = Depends(get_token_provider),
    uow_factory: Callable[[], UnitOfWork] = Depends(get_unit_of_work_factory),
) -> RepositorySettingsResponse:
    repository = visible_repository(repository_id, signed_in, catalog, oauth, uow_factory)
    settings = RepositorySettings(
        auto_review_enabled=body.auto_review_enabled,
        branch_filter=tuple(body.branch_filter),
        min_severity=body.min_severity,
    )
    # Права — мимо кэша, как у выключения: `user_repository` кэш видимости не читает.
    now = datetime.now(UTC)
    outcome = _as_user(
        signed_in,
        oauth,
        lambda user: connecting.save_repository_settings(catalog, uow_factory(), user, repository, settings, now),
    )
    if outcome is connecting.ConnectRefusal.MANAGE_REQUIRED:
        raise ApiError(
            HTTPStatus.FORBIDDEN,
            ErrorCode.REPOSITORY_MANAGE_REQUIRED,
            "Менять настройки ревью может только пользователь с правом admin или maintain на репозиторий.",
        )
    if isinstance(outcome, connecting.ConnectRefusal):
        # GitHub не отдал репозиторий — ответ тот же, что у невидимого.
        raise _repository_not_found()
    # Ответ — запись, какой её сохранила база, а не тело запроса.
    return _settings_response(outcome)


@router.get(
    "/{repository_id}/rules",
    response_model=RulesStatusResponse,
    summary="Статус правил ревью репозитория",
    response_description="Исход загрузки `.review/rules.md`.",
    description=_RULES_DESCRIPTION,
    responses={**ERRORS_404_REPOSITORY_OR_NOT_IN_INSTALLATION, **ERRORS_422, **ERRORS_502_SCM_VISIBILITY},
)
def get_repository_rules(
    repository_id: RepositoryIdPath,
    signed_in: SignedIn = Depends(current_session),
    catalog: InstallationCatalog = Depends(get_visibility_catalog),
    # Номер установки — мимо кэша видимости, как у диффа прогона: после переустановки
    # приложения он другой.
    installations: InstallationCatalog = Depends(get_installation_catalog),
    oauth: OAuthProvider = Depends(get_token_provider),
    uow_factory: Callable[[], UnitOfWork] = Depends(get_unit_of_work_factory),
    vcs: VcsGateway = Depends(get_cabinet_vcs_gateway),
    cache: CacheStore = Depends(get_rules_cache),
    settings: Settings = Depends(get_settings),
) -> RulesStatusResponse:
    # Синхронная `def`: шлюз и кэш блокирующие, FastAPI уводит ручку в threadpool.
    stored, host = _visible_with_host(repository_id, signed_in, catalog, oauth, uow_factory)
    # Ветка и имя — из ответа GitHub, как в GET /repositories: ветку по умолчанию могли сменить
    # или переименовать после подключения.
    repository = replace(stored, full_name=host.full_name, default_branch=host.default_branch)
    try:
        rules = load_default_branch_rules(
            repository,
            installations=installations,
            vcs=vcs,
            cache=cache,
            max_bytes=settings.rules_max_bytes,
            ttl_seconds=settings.rules_cache_ttl_seconds,
            now=datetime.now(UTC),
        )
    except RepositoryNotInstalled:
        raise ApiError(
            HTTPStatus.NOT_FOUND,
            ErrorCode.REPOSITORY_NOT_IN_INSTALLATION,
            "Приложение не установлено на репозиторий или установка приостановлена.",
        ) from None
    return _rules_response(rules, repository.default_branch)


def _rules_response(rules: RepoRules, branch: str) -> RulesStatusResponse:
    """Вариант ответа по статусу: поля, которых у статуса нет, в ответ не идут."""
    file = {"branch": branch, "path": RULES_PATH}
    rejected = [RejectedRule(name=r.name, reason=r.reason) for r in rules.rejected]
    variant: RulesLoaded | RulesEmpty | RulesAbsent | RulesTooLarge | RulesUnavailable
    match rules.status:
        case RulesStatus.LOADED:
            variant = RulesLoaded(
                status=RulesStatus.LOADED,
                rules=[
                    RepoRule(name=r.name, patterns=list(r.patterns), excludes=list(r.excludes), checks=list(r.checks))
                    for r in rules.rules
                ],
                rejected=rejected,
                fetched_at=_fetched_at(rules),
                **file,
            )
        case RulesStatus.EMPTY:
            variant = RulesEmpty(status=RulesStatus.EMPTY, rejected=rejected, fetched_at=_fetched_at(rules), **file)
        case RulesStatus.ABSENT:
            variant = RulesAbsent(status=RulesStatus.ABSENT, **file)
        case RulesStatus.TOO_LARGE:
            variant = RulesTooLarge(status=RulesStatus.TOO_LARGE, **file)
        case RulesStatus.UNAVAILABLE:
            variant = RulesUnavailable(status=RulesStatus.UNAVAILABLE, **file)
    return RulesStatusResponse(variant)


def _fetched_at(rules: RepoRules) -> datetime:
    # Разобранный файл всегда получен от GitHub: `None` бывает только у `unavailable`.
    if rules.fetched_at is None:
        raise ValueError(f"у статуса {rules.status} нет времени получения файла")
    return rules.fetched_at
