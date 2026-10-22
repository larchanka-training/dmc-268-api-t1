"""Прогон ревью: чтение вместе с находками и ручной запуск (пока заглушка).

Один связный ответ, не два эндпоинта: прогон без находок бессмысленно
смотреть отдельно от них (design.md change'а add-review-rest-api).

`ReviewJob` — общая модель ответа ручного запуска и приёма вебхука, лежит в
`app/api/schemas.py`.
"""

from dataclasses import asdict
from http import HTTPStatus
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query
from fastapi.exceptions import RequestValidationError
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.api.dependencies import get_unit_of_work
from app.api.errors import (
    ERRORS_403_ORIGIN,
    ERRORS_404_REVIEW,
    ERRORS_409_REVIEW_ACTIVE,
    ERRORS_422,
    ERRORS_501_NOT_IMPLEMENTED,
    ApiError,
)
from app.api.pagination import DEFAULT_LIMIT, Limit, Offset
from app.api.problems import ErrorCode
from app.api.schemas import (
    FAILED_STAGE_DESCRIPTION,
    STREAM_HEADERS_DOC,
    FailedStage,
    MergeRequestBase,
    RepositoryBase,
    ReviewJob,
    StrictRequest,
    UtcDatetime,
)
from app.api.session import SESSION_REQUIRED, SESSION_RESPONSES
from app.application.ports import UnitOfWork
from app.domain.enums import (
    CommentKind,
    DiffSide,
    FindingCategory,
    FindingSeverity,
    Provider,
    ReviewRunStatus,
    TriggerSource,
    VerificationOutcome,
)
from app.domain.provider_links import merge_request_url

# Задачи на реализацию ручного запуска пока нет — ссылка появится в описании операции.
_CREATE_REVIEW_DESCRIPTION = (
    "Не реализовано: отвечает 501 NOT_IMPLEMENTED. Описана заранее, чтобы фронтенд "
    "генерировал типы; план ручного запуска — PIPELINE_SPEC.md §4. Сейчас ревью запускает "
    "только вебхук. Когда ручка заработает: 202 с ReviewJob, 409 REVIEW_ALREADY_ACTIVE, "
    "если по этому коммиту прогон уже идёт. Требует сессию и Origin сервиса; Origin "
    "проверяется раньше сессии и разбора тела."
)

# Решения 2 и 4 в dmc-268-ui-t1#37; реализация — отдельными задачами, ссылка появится здесь.
_DIFF_DESCRIPTION = (
    "Не реализовано: отвечает 501 NOT_IMPLEMENTED. Описана заранее, чтобы фронтенд генерировал типы "
    "(задача #82). Когда ручка заработает: полный дифф запроса на изменение у провайдера между base_sha "
    "и head_sha прогона; 404 REVIEW_NOT_FOUND, если прогона нет. Требует сессию."
)
_STEPS_DESCRIPTION = (
    "Не реализовано: отвечает 501 NOT_IMPLEMENTED. Описана заранее, чтобы фронтенд генерировал типы "
    "(задача #82). Когда ручка заработает: шаги прогона по порядку — этап, статус, время, токены, код "
    "ошибки; без запросов и ответов модели. 404 REVIEW_NOT_FOUND, если прогона нет. Требует сессию."
)

# Видимость прогонов — решение А в dmc-268-ui-t1#43: одно правило на список и поток.
_VISIBILITY = (
    "Пользователю видны только прогоны репозиториев, к которым у него есть доступ на GitHub "
    "(любая роль), — то же правило, что у GET /repositories/available."
)
_LIST_DESCRIPTION = (
    "Не реализовано: отвечает 501 NOT_IMPLEMENTED. Описана заранее, чтобы фронтенд генерировал типы "
    "(задача #103). Требует сессию; параметры запроса проверяются до ответа 501. Когда ручка "
    "заработает: снимок списка прогонов ревью, сначала новые — по created_at по убыванию, затем по id. "
    + _VISIBILITY
    + " Фильтры складываются через «и»: status — один или несколько статусов (параметр повторяется); "
    "active=true — только активные (queued, building_context, analysing, publishing), active=false — "
    "только завершённые (completed, failed, cancelled), вместе со status — пересечение; repository_id — "
    "прогоны одного репозитория; author — точное совпадение логина автора запроса на изменение; "
    "q — поиск без учёта регистра по заголовку, номеру (42 или #42), автору и исходной ветке запроса "
    "на изменение, пустая строка — без поиска; created_from (включительно) и created_to "
    "(исключительно) — диапазон по created_at прогона, created_from позже created_to — 422 "
    "VALIDATION_ERROR. Постранично: limit выше 200 не отклоняется, а приводится к 200. total — "
    "сколько прогонов подходит под фильтры без учёта limit и offset. Находок и комментариев в строке "
    "нет: они — в GET /reviews/{review_run_id}. Изменения после снимка приходят потоком "
    "GET /reviews/events."
)
_STREAM_DESCRIPTION = (
    "Не реализовано: отвечает 501 NOT_IMPLEMENTED. Описана заранее, чтобы фронтенд генерировал типы "
    "(задача #103). Требует сессию. Когда ручка заработает: один поток Server-Sent Events на весь "
    "кабинет — изменения прогонов ревью после снимка GET /reviews. Тип события — в поле `event:`, "
    "данные — JSON модели из схемы в `data:` одной строкой: `run.created` — ReviewRunListItem (новый "
    "прогон, та же строка, что в списке), `run.status_changed` — ReviewRunStatusChangedEvent, "
    "`run.finished` — ReviewRunFinishedEvent (прогон пришёл в итоговый статус completed, failed или "
    "cancelled), `ping` с `data: {}` — в тишине, не реже раза в 15 секунд. Поток начинается со "
    "строки `retry: 3000`. " + _VISIBILITY + " События уходят в момент коммита записи прогона "
    "(PostgreSQL NOTIFY из транзакции записи): откаченная запись события не даёт. Истории сервер "
    "не хранит: событие, пропущенное при обрыве, не повторяется, поэтому после переподключения "
    "клиент перезапрашивает GET /reviews. При обрыве своей подписки на уведомления сервер сам "
    "закрывает поток, чтобы клиент перезапросил список."
)

# Сессия — на весь роутер: исключения видны здесь, а не в middleware.
router = APIRouter(
    prefix="/api/v1/reviews",
    tags=["reviews"],
    dependencies=SESSION_REQUIRED,
    responses=SESSION_RESPONSES,
)


class CreateReviewRequest(StrictRequest):
    """Запрос ручного запуска ревью запроса на изменение."""

    # strict у модели не пропустил бы enum строкой: тело приходит разобранным JSON, а в
    # Python-режиме strict ждёт экземпляр enum. Для enum-полей — точечно строкой из списка.
    provider: Provider = Field(strict=False, description="Хостинг репозитория.", examples=["github"])
    provider_repository_id: str = Field(
        min_length=1,
        max_length=255,
        description="Идентификатор репозитория у провайдера (repositories.provider_id).",
        examples=["123456789"],
    )
    pull_request_number: int = Field(
        ge=1, le=2_147_483_647, description="Номер запроса на изменение у провайдера.", examples=[123]
    )
    trigger: TriggerSource = Field(strict=False, description="Источник запуска.", examples=["manual"])


class RuleRefResponse(BaseModel):
    """Пункт правила владельца, по которому выпущена находка."""

    name: str = Field(description="Имя правила из .review/rules.md.", examples=["Error Handling Standards"])
    check: int = Field(description="Номер пункта правила, с 1.", examples=[2])
    check_text: str = Field(description="Текст пункта на момент прогона.")


class FindingResponse(BaseModel):
    """Находка ревью, привязанная к строке диффа."""

    id: UUID
    file_path: str = Field(description="Путь файла от корня репозитория.", examples=["app/main.py"])
    side: DiffSide = Field(description="Сторона диффа, к которой привязана находка.", examples=["new"])
    old_line: int | None = Field(description="Строка в старой версии файла, с 1; null, если находка на новой стороне.")
    new_line: int | None = Field(description="Строка в новой версии файла, с 1; null, если находка на старой стороне.")
    category: FindingCategory = Field(description="Ось ревью, к которой относится находка.", examples=["security"])
    severity: FindingSeverity = Field(description="Насколько находка важна.", examples=["high"])
    message: str = Field(description="Текст замечания.")
    suggestion: str | None = Field(description="Предлагаемая правка; null, если её нет.")
    confidence: float | None = Field(
        description="Уверенность модели в находке, обычно от 0 до 1; null, если модель её не дала."
    )
    rule: RuleRefResponse | None = Field(
        description="Пункт правила владельца, по которому выпущена находка; null - находка по стандартным критериям."
    )


class RepositorySummary(RepositoryBase):
    """Репозиторий запроса на изменение: ровно то, что нужно, чтобы его назвать и сослаться."""


class MergeRequestSummary(MergeRequestBase):
    """Запрос на изменение, который ревьюит прогон, вместе с его репозиторием."""

    url: str | None = Field(
        description=(
            "Страница запроса на изменение у провайдера; строится из провайдера, полного имени "
            "репозитория и номера. null, если формат ссылки провайдера не поддержан."
        ),
        examples=["https://github.com/octo-org/api/pull/42"],
    )
    repository: RepositorySummary = Field(description="Репозиторий запроса на изменение.")


class PublishedCommentResponse(BaseModel):
    """Комментарий, который прогон опубликовал на хостинге."""

    id: UUID
    finding_id: UUID | None = Field(
        description="Находка, к которой относится комментарий или ответ в тред; null у сводки."
    )
    provider_comment_id: str = Field(description="Идентификатор комментария у провайдера.", examples=["2345678901"])
    kind: CommentKind = Field(
        description="Форма комментария: сводка, комментарий к строке или ответ в тред.", examples=["inline"]
    )
    published_at: UtcDatetime = Field(description="Когда комментарий опубликован.")


class ReviewRunResponse(BaseModel):
    """Прогон ревью вместе с его находками."""

    id: UUID
    merge_request_id: UUID = Field(description="Запрос на изменение, к которому относится прогон.")
    merge_request: MergeRequestSummary = Field(description="Запрос на изменение прогона: тот же, что merge_request_id.")
    head_sha: str = Field(
        description="Коммит, который ревьюится.", examples=["a1b2c3d4e5f6789012345678abcdef0123456789"]
    )
    base_sha: str | None = Field(
        description="Коммит, относительно которого считается дифф; null, пока воркер его не узнал.",
        examples=["fedcba0987654321fedcba0987654321fedcba09"],
    )
    status: ReviewRunStatus = Field(description="Текущий статус прогона.", examples=["completed"])
    trigger: TriggerSource = Field(description="Что запустило прогон.", examples=["webhook"])
    model: str | None = Field(description="Модель LLM, которая провела ревью; null до анализа.")
    tokens_used: int | None = Field(description="Токенов LLM потрачено; null до анализа.")
    rejected_findings: int = Field(ge=0, description="Сколько находок отброшено: привязка вне изменённых строк диффа.")
    verification: VerificationOutcome | None = Field(
        description="Итог второго прохода модели (Reflection Pass); null, пока анализ не завершён.",
        examples=["verified"],
    )
    verifier_dropped_findings: int = Field(ge=0, description="Сколько находок отбросил верификатор.")
    duration_seconds: float | None = Field(description="Длительность прогона в секундах; null, пока не завершён.")
    failure_reason: str | None = Field(description="Почему прогон упал; null, если не падал.")
    failed_stage: FailedStage | None = Field(description=FAILED_STAGE_DESCRIPTION)
    last_progress_at: UtcDatetime = Field(description="Время последнего перехода состояния.")
    created_at: UtcDatetime = Field(description="Когда прогон создан.")
    updated_at: UtcDatetime = Field(description="Когда запись последний раз менялась.")
    findings: list[FindingResponse] = Field(description="Находки прогона: по времени создания, затем по id.")
    published_comments: list[PublishedCommentResponse] = Field(
        description="Комментарии, опубликованные прогоном на хостинге: по времени публикации, затем по id."
    )


class ReviewRunListItem(BaseModel):
    """Прогон ревью строкой списка: без находок и комментариев.

    Те же данные — у события `run.created` потока GET /reviews/events.
    """

    id: UUID
    status: ReviewRunStatus = Field(description="Текущий статус прогона.", examples=["analysing"])
    trigger: TriggerSource = Field(description="Что запустило прогон.", examples=["webhook"])
    head_sha: str = Field(
        description="Коммит, который ревьюится.", examples=["a1b2c3d4e5f6789012345678abcdef0123456789"]
    )
    created_at: UtcDatetime = Field(description="Когда прогон создан.")
    updated_at: UtcDatetime = Field(description="Когда запись последний раз менялась.")
    duration_seconds: float | None = Field(description="Длительность прогона в секундах; null, пока не завершён.")
    failed_stage: FailedStage | None = Field(description=FAILED_STAGE_DESCRIPTION)
    findings_count: int = Field(ge=0, description="Сколько находок у прогона.")
    merge_request: MergeRequestSummary = Field(description="Запрос на изменение, который ревьюит прогон.")


class ReviewRunListResponse(BaseModel):
    """Страница списка прогонов ревью."""

    items: list[ReviewRunListItem] = Field(description="Прогоны: по created_at по убыванию, затем по id.")
    total: int = Field(
        ge=0, description="Сколько прогонов подходит под фильтры, без учёта limit и offset."
    )


class ReviewRunStatusChangedEvent(BaseModel):
    """Данные события `run.status_changed` потока GET /reviews/events: прогон сменил статус.

    Тело ответа — текст SSE, а не этот JSON; в схему модель попадает отдельным компонентом
    (`app/api/openapi.py`, `extra_models`).
    """

    id: UUID = Field(description="Прогон, о котором событие.")
    status: ReviewRunStatus = Field(description="Новый статус прогона.", examples=["analysing"])
    failed_stage: FailedStage | None = Field(description=FAILED_STAGE_DESCRIPTION)


class ReviewRunFinishedEvent(BaseModel):
    """Данные события `run.finished` потока GET /reviews/events: прогон пришёл в итоговый статус.

    Тело ответа — текст SSE, а не этот JSON; в схему модель попадает отдельным компонентом
    (`app/api/openapi.py`, `extra_models`).
    """

    id: UUID = Field(description="Прогон, о котором событие.")
    status: ReviewRunStatus = Field(
        description="Итоговый статус прогона: completed, failed или cancelled.", examples=["completed"]
    )
    duration_seconds: float | None = Field(description="Длительность прогона в секундах; null, если не измерена.")
    findings_count: int = Field(ge=0, description="Сколько находок у прогона.")


_STREAM_DOC: dict[int | str, dict[str, Any]] = {
    HTTPStatus.OK: {
        "description": (
            "Поток событий; данные: `run.created` — ReviewRunListItem, `run.status_changed` — "
            "ReviewRunStatusChangedEvent, `run.finished` — ReviewRunFinishedEvent."
        ),
        "content": {
            "text/event-stream": {
                "schema": {
                    "type": "string",
                    "description": (
                        "Текст SSE: `event: run.created`, `run.status_changed` или `run.finished` с JSON "
                        "модели события в `data` или `event: ping`."
                    ),
                }
            }
        },
        "headers": STREAM_HEADERS_DOC,
    },
}


@router.post(
    "",
    status_code=HTTPStatus.ACCEPTED,
    response_model=ReviewJob,
    summary="Запустить ревью вручную",
    response_description="ReviewJob создан и поставлен в очередь.",
    description=_CREATE_REVIEW_DESCRIPTION,
    responses={**ERRORS_403_ORIGIN, **ERRORS_409_REVIEW_ACTIVE, **ERRORS_422, **ERRORS_501_NOT_IMPLEMENTED},
)
def create_review(body: CreateReviewRequest) -> ReviewJob:
    raise ApiError(HTTPStatus.NOT_IMPLEMENTED, ErrorCode.NOT_IMPLEMENTED, "Ручной запуск ревью ещё не реализован.")


def _reject_reversed_range(created_from: UtcDatetime | None, created_to: UtcDatetime | None) -> None:
    """Начало диапазона позже конца — `422`, как ошибка проверки параметра."""
    if created_from is not None and created_to is not None and created_from > created_to:
        raise RequestValidationError(
            [{"type": "range_reversed", "loc": ("query", "created_to"), "msg": "", "input": None}]
        )


# `/events` и список объявлены раньше `/{review_run_id}`: маршруты сверяются по порядку,
# и `events` иначе попал бы в параметр пути и получил 422 «ожидается UUID».
@router.get(
    "",
    response_model=ReviewRunListResponse,
    summary="Список прогонов ревью",
    response_description="Страница прогонов и общее число подходящих под фильтры.",
    description=_LIST_DESCRIPTION,
    responses={**ERRORS_422, **ERRORS_501_NOT_IMPLEMENTED},
)
def list_reviews(
    status: Annotated[
        list[ReviewRunStatus] | None,
        Query(description="Статусы прогона; параметр повторяется: ?status=queued&status=analysing."),
    ] = None,
    active: Annotated[
        bool | None,
        Query(description="true — только активные прогоны, false — только завершённые; не задан — все."),
    ] = None,
    repository_id: Annotated[UUID | None, Query(description="Только прогоны этого репозитория.")] = None,
    author: Annotated[
        str | None,
        Query(max_length=255, description="Логин автора запроса на изменение, точное совпадение."),
    ] = None,
    q: Annotated[
        str | None,
        Query(
            max_length=200,
            description=(
                "Поиск без учёта регистра по заголовку, номеру (42 или #42), автору и исходной ветке "
                "запроса на изменение; пустая строка — без поиска."
            ),
        ),
    ] = None,
    created_from: Annotated[
        UtcDatetime | None, Query(description="Прогоны, созданные не раньше этого времени (включительно).")
    ] = None,
    created_to: Annotated[
        UtcDatetime | None, Query(description="Прогоны, созданные раньше этого времени (исключительно).")
    ] = None,
    limit: Limit = DEFAULT_LIMIT,
    offset: Offset = 0,
) -> ReviewRunListResponse:
    _reject_reversed_range(created_from, created_to)
    raise ApiError(HTTPStatus.NOT_IMPLEMENTED, ErrorCode.NOT_IMPLEMENTED, "Список прогонов ревью ещё не реализован.")


@router.get(
    "/events",
    response_class=StreamingResponse,
    summary="Поток изменений прогонов ревью (Server-Sent Events)",
    description=_STREAM_DESCRIPTION,
    responses={**_STREAM_DOC, **ERRORS_501_NOT_IMPLEMENTED},
)
def stream_reviews_events() -> StreamingResponse:
    raise ApiError(HTTPStatus.NOT_IMPLEMENTED, ErrorCode.NOT_IMPLEMENTED, "Поток изменений прогонов ещё не реализован.")


@router.get(
    "/{review_run_id}",
    response_model=ReviewRunResponse,
    summary="Прогон ревью вместе с находками",
    response_description="Прогон, его запрос на изменение, находки и опубликованные комментарии.",
    description=(
        "Требует сессию. Находки — в стабильном порядке: по времени создания, затем по id. Прогон без находок — "
        "200 с пустым findings. Опубликованные комментарии — по времени публикации, затем по id; пока прогон "
        "ничего не опубликовал — пустой published_comments."
    ),
    responses={**ERRORS_404_REVIEW, **ERRORS_422},
)
def get_review(
    review_run_id: Annotated[UUID, Path(description="Идентификатор прогона ревью.")],
    uow: UnitOfWork = Depends(get_unit_of_work),
) -> ReviewRunResponse:
    run = uow.review_runs.get(review_run_id)
    if run is None:
        raise ApiError(HTTPStatus.NOT_FOUND, ErrorCode.REVIEW_NOT_FOUND, "Прогона ревью с таким id нет.")

    # Запрос на изменение и репозиторий держат внешние ключи с RESTRICT: их отсутствие —
    # нарушение целостности базы, а не «не найдено», поэтому 500, а не 404.
    merge_request = uow.merge_requests.get(run.merge_request_id)
    if merge_request is None:
        raise RuntimeError(f"У прогона {run.id} нет запроса на изменение {run.merge_request_id}.")
    repository = uow.repositories.get(merge_request.repository_id)
    if repository is None:
        raise RuntimeError(f"У запроса на изменение {merge_request.id} нет репозитория {merge_request.repository_id}.")

    findings = uow.findings.list_for_run(review_run_id)
    comments = uow.published_comments.list_for_run(review_run_id)
    return ReviewRunResponse.model_validate(
        {
            **asdict(run),
            "merge_request": {
                **asdict(merge_request),
                "url": merge_request_url(repository.provider, repository.full_name, merge_request.number),
                "repository": asdict(repository),
            },
            "findings": [{**asdict(f), **asdict(f.anchor)} for f in findings],
            "published_comments": [asdict(c) for c in comments],
        }
    )


class DiffLineResponse(BaseModel):
    """Строка ханка."""

    kind: Literal["added", "removed", "context"] = Field(description="Добавлена, удалена или контекст.")
    old_line: int | None = Field(description="Номер в старой версии файла, с 1; null у добавленной строки.")
    new_line: int | None = Field(description="Номер в новой версии файла, с 1; null у удалённой строки.")
    content: str = Field(description="Текст строки без префикса +, - или пробела.")


class DiffHunkResponse(BaseModel):
    """Ханк unified diff."""

    header: str = Field(description="Заголовок ханка после @@: обычно сигнатура окружающего кода.")
    old_start: int = Field(description="Первая строка ханка в старой версии файла.")
    old_count: int = Field(description="Строк ханка в старой версии файла.")
    new_start: int = Field(description="Первая строка ханка в новой версии файла.")
    new_count: int = Field(description="Строк ханка в новой версии файла.")
    lines: list[DiffLineResponse] = Field(description="Строки ханка по порядку.")


class DiffFileResponse(BaseModel):
    """Изменённый файл запроса на изменение."""

    path: str = Field(description="Путь файла от корня репозитория.", examples=["app/main.py"])
    previous_path: str | None = Field(description="Путь до переименования; null, если файл не переименован.")
    status: Literal["added", "modified", "deleted", "renamed"] = Field(description="Что произошло с файлом.")
    is_binary: bool = Field(description="Бинарный файл: ханков нет.")
    language: str | None = Field(description="Язык для подсветки синтаксиса; null, если не определён.")
    additions: int = Field(ge=0, description="Добавленных строк.")
    deletions: int = Field(ge=0, description="Удалённых строк.")
    hunks: list[DiffHunkResponse] = Field(description="Ханки файла по порядку.")


class ReviewDiffResponse(BaseModel):
    """Дифф запроса на изменение, который ревьюил прогон."""

    files: list[DiffFileResponse] = Field(description="Изменённые файлы, включая файлы без замечаний.")


class ReviewRunStepResponse(BaseModel):
    """Шаг воркера в прогоне."""

    stage: Literal["fetching_diff", "building_context", "analysing", "verifying", "publishing"] = Field(
        description="Этап прогона.", examples=["analysing"]
    )
    status: Literal["running", "completed", "failed"] = Field(description="Состояние шага.")
    started_at: UtcDatetime = Field(description="Когда шаг начался.")
    duration_seconds: float | None = Field(description="Длительность шага; null, пока шаг идёт.")
    tokens_used: int | None = Field(description="Токенов LLM потрачено на шаге; null, если шаг модель не вызывал.")
    error_code: str | None = Field(description="Код ошибки шага; null, если шаг не падал.", examples=["LLM_TIMEOUT"])


class ReviewRunStepListResponse(BaseModel):
    """Шаги прогона."""

    items: list[ReviewRunStepResponse] = Field(description="Шаги по порядку начала.")


@router.get(
    "/{review_run_id}/diff",
    response_model=ReviewDiffResponse,
    summary="Дифф прогона",
    response_description="Изменённые файлы с ханками.",
    description=_DIFF_DESCRIPTION,
    responses={**ERRORS_404_REVIEW, **ERRORS_422, **ERRORS_501_NOT_IMPLEMENTED},
)
def get_review_diff(
    review_run_id: Annotated[UUID, Path(description="Идентификатор прогона ревью.")],
) -> ReviewDiffResponse:
    raise ApiError(HTTPStatus.NOT_IMPLEMENTED, ErrorCode.NOT_IMPLEMENTED, "Дифф прогона ещё не реализован.")


@router.get(
    "/{review_run_id}/steps",
    response_model=ReviewRunStepListResponse,
    summary="Шаги прогона",
    response_description="Шаги воркера по порядку.",
    description=_STEPS_DESCRIPTION,
    responses={**ERRORS_404_REVIEW, **ERRORS_422, **ERRORS_501_NOT_IMPLEMENTED},
)
def get_review_steps(
    review_run_id: Annotated[UUID, Path(description="Идентификатор прогона ревью.")],
) -> ReviewRunStepListResponse:
    raise ApiError(HTTPStatus.NOT_IMPLEMENTED, ErrorCode.NOT_IMPLEMENTED, "Шаги прогона ещё не реализованы.")
