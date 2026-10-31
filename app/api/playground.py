"""Песочница правил: разбор `.review/rules.md` и симуляция ревью на своём коде (пока заглушки).

Пользователь вставляет фрагмент кода или дифф и текст правил и видит, что скажет ревью,
без запроса на изменение и без публикации (#287). Ручки описаны заранее, чтобы фронтенд
генерировал типы (dmc-268-ui-t1#112); реализация - #288.
"""

from http import HTTPStatus
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Path
from pydantic import AfterValidator, BaseModel, Field, StringConstraints

from app.api.errors import (
    ERRORS_403_ORIGIN,
    ERRORS_404_SIMULATION,
    ERRORS_413_BODY,
    ERRORS_422,
    ERRORS_429_RATE_LIMITED,
    ERRORS_501_NOT_IMPLEMENTED,
    ApiError,
)
from app.api.problems import ErrorCode
from app.api.schemas import (
    FileContextResponse,
    FindingBase,
    StrictRequest,
    UtcDatetime,
    VerifierVerdictResponse,
    without_nul,
)
from app.api.session import SESSION_REQUIRED, SESSION_RESPONSES
from app.domain.enums import (
    DiffSide,
    DiscardStage,
    FindingCategory,
    FindingSeverity,
    SimulationStatus,
    VerificationOutcome,
)
from app.domain.repo_rules import RULES_PATH

# Потолки входа. Правила - не больше лимита, с которым ревью читает `.review/rules.md`
# (`RULES_MAX_BYTES`, 32 КиБ); код и дифф - не больше пакета контекста модели
# (`CONTEXT_MAX_TOKENS` × 4 байта, 128 КиБ): больше модель всё равно не увидит.
RULES_TEXT_MAX_LENGTH = 32_768
SOURCE_MAX_LENGTH = 131_072
FILE_PATH_MAX_LENGTH = 1_024
PARSE_PATHS_MAX_ITEMS = 100

_NOT_IMPLEMENTED = "Не реализовано: отвечает 501 NOT_IMPLEMENTED. Описана заранее, чтобы фронтенд генерировал типы (задача #288). "
_PARSE_DESCRIPTION = (
    _NOT_IMPLEMENTED
    + "Требует сессию и Origin сервиса; Origin проверяется раньше сессии и разбора тела, тело - раньше "
    "ответа 501. Когда ручка заработает: 200 с разбором текста тем же парсером, что у ревью запроса на "
    "изменение, без вызова модели, - для подсказок в редакторе песочницы. Принятые правила - со строкой "
    "заголовка, пунктами и файлами из paths, к которым правило применимо; отклонённые разделы - с причиной."
)
_CREATE_DESCRIPTION = (
    _NOT_IMPLEMENTED
    + "Требует сессию и Origin сервиса; Origin проверяется раньше сессии и разбора тела, тело - раньше "
    "ответа 501. Когда ручка заработает: 202 с симуляцией в статусе queued. Воркер прогонит вход тем же "
    "конвейером, что ревью запроса на изменение, - сборка контекста, ревью, отсев, верификатор, - без "
    "провайдера VCS и без публикации. Фрагмент кода (source.kind = code) ревьюится как новый файл, все "
    "строки - добавленные; file_path нужен, чтобы сработали pattern правил и выбрался язык AST. Дифф "
    "(source.kind = diff) берёт файлы и пути из своих заголовков; неразборчивый дифф - 422. Пустой rules - "
    "ревью по стандартным критериям. Ход и итог приходят событиями simulation.* потока GET /events и "
    "читаются GET /simulations/{simulation_id}. Каждый запуск вызывает модель, поэтому частота запусков "
    "ограничена на пользователя: 429 RATE_LIMITED с Retry-After."
)
_GET_DESCRIPTION = (
    _NOT_IMPLEMENTED
    + "Требует сессию; id проверяется раньше ответа 501. Когда ручка заработает: симуляция, которую "
    "запустил этот пользователь, - статус, замечания, отброшенные замечания и контекст модели. Чужая, "
    "несуществующая и удалённая по сроку хранения (expires_at) симуляция - 404 SIMULATION_NOT_FOUND без "
    "различия."
)

# Путь файла от корня репозитория - как в диффе: по нему сопоставляются `pattern` правил.
FilePath = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=FILE_PATH_MAX_LENGTH),
    AfterValidator(without_nul),
]
RulesText = Annotated[
    str,
    Field(
        max_length=RULES_TEXT_MAX_LENGTH,
        description=f"Текст {RULES_PATH}; пустая строка - правил нет, ревью по стандартным критериям.",
    ),
    AfterValidator(without_nul),
]
SourceText = Annotated[str, Field(min_length=1, max_length=SOURCE_MAX_LENGTH), AfterValidator(without_nul)]

router = APIRouter(
    prefix="/api/v1",
    tags=["playground"],
    dependencies=SESSION_REQUIRED,
    responses=SESSION_RESPONSES,
)


class RulesParseRequest(StrictRequest):
    """Текст правил для разбора и файлы, к которым их примерить."""

    rules: RulesText
    paths: list[FilePath] = Field(
        default=[],
        max_length=PARSE_PATHS_MAX_ITEMS,
        description="Пути файлов от корня репозитория, к которым примерить правила; пустой список - без примерки.",
        examples=[["app/billing.py"]],
    )


class ParsedCheckResponse(BaseModel):
    """Пункт правила - одна проверка."""

    number: int = Field(ge=1, description="Номер пункта - его позиция среди проверок правила, с 1; по нему ссылается находка.")
    text: str = Field(description="Текст пункта.")
    line: int = Field(ge=1, description="Строка пункта в тексте правил, с 1.")


class ParsedRuleResponse(BaseModel):
    """Раздел, принятый разбором как правило."""

    name: str = Field(description="Имя правила - заголовок раздела второго уровня.", examples=["Error Handling Standards"])
    line: int = Field(ge=1, description="Строка заголовка раздела в тексте правил, с 1.")
    patterns: list[str] = Field(description="Glob-паттерны файлов, к которым правило применяется.", examples=[["app/**/*.py"]])
    excludes: list[str] = Field(description="Glob-паттерны исключений; пустой список - не исключено ничего.")
    checks: list[ParsedCheckResponse] = Field(description="Пункты правила по порядку.")
    matched_paths: list[str] = Field(
        description="Файлы из paths запроса, к которым правило применимо, в порядке запроса.",
        examples=[["app/billing.py"]],
    )


class ParsedRejectedRuleResponse(BaseModel):
    """Раздел, не ставший правилом."""

    name: str = Field(description="Заголовок раздела.")
    line: int = Field(ge=1, description="Строка заголовка раздела в тексте правил, с 1.")
    reason: str = Field(description="Чего разделу не хватило - по-русски, для показа пользователю.", examples=["нет проверок"])


class RulesParseResponse(BaseModel):
    """Разбор текста правил: что станет правилами ревью и что отброшено."""

    rules: list[ParsedRuleResponse] = Field(description="Принятые правила по порядку в тексте.")
    rejected: list[ParsedRejectedRuleResponse] = Field(description="Разделы, не ставшие правилами, по порядку в тексте.")


class CodeSource(StrictRequest):
    """Фрагмент кода: ревьюится как новый файл, все строки - добавленные."""

    kind: Literal["code"]
    file_path: FilePath = Field(
        description="Путь файла от корня репозитория: по нему сопоставляются pattern правил и выбирается язык AST.",
        examples=["app/billing.py"],
    )
    content: SourceText = Field(description="Текст файла.")


class DiffSource(StrictRequest):
    """Unified diff: файлы, пути и строки - из его заголовков."""

    kind: Literal["diff"]
    diff: SourceText = Field(description="Unified diff одного или нескольких файлов, как его отдаёт git diff.")


class SimulationRequest(StrictRequest):
    """Что отревьюить и по каким правилам."""

    source: Annotated[CodeSource | DiffSource, Field(discriminator="kind")] = Field(
        description="Что ревьюить: фрагмент кода (kind = code) или unified diff (kind = diff)."
    )
    rules: RulesText


class SimulationFindingResponse(FindingBase):
    """Замечание симуляции: те же поля, что у находки прогона, и вердикт верификатора."""

    verdict: VerifierVerdictResponse | None = Field(
        description="Решение верификатора по замечанию; null, если верификатор не отработал (verification не verified)."
    )


class RuleMentionResponse(BaseModel):
    """Ссылка на пункт правила в том виде, в каком её дала модель, - до проверки."""

    name: str = Field(description="Имя правила, как его написала модель.")
    check: int = Field(description="Номер пункта, как его написала модель.")


class DiscardedFindingResponse(BaseModel):
    """Замечание модели, которое не дошло до результата, и почему."""

    stage: DiscardStage = Field(
        description="Где отброшено: screening - детерминированный отсев, verification - верификатор.",
        examples=["screening"],
    )
    reason: str = Field(
        description="Почему - по-русски, для показа пользователю: причина отсева или слова верификатора.",
        examples=["правило «Naming Consistency» не применимо к app/billing.py"],
    )
    file_path: str = Field(description="Путь файла, как его дала модель.", examples=["app/billing.py"])
    side: DiffSide = Field(description="Сторона диффа, как её дала модель.", examples=["new"])
    line: int = Field(description="Строка на стороне side, как её дала модель; может быть вне диффа.")
    category: FindingCategory = Field(description="Ось ревью.", examples=["readability"])
    severity: FindingSeverity = Field(description="Насколько замечание важно по мнению модели.", examples=["low"])
    message: str = Field(description="Текст замечания модели.")
    rule: RuleMentionResponse | None = Field(
        description="Ссылка на правило, как её дала модель; null - замечание по стандартным критериям."
    )


class SimulationSummary(BaseModel):
    """Симуляция в событии потока: что изменилось. Полный результат - GET /simulations/{simulation_id}."""

    id: UUID
    status: SimulationStatus = Field(description="Текущий статус симуляции.", examples=["analysing"])
    updated_at: UtcDatetime = Field(description="Когда симуляция последний раз менялась.")


class SimulationResponse(BaseModel):
    """Симуляция ревью в песочнице правил: ход, замечания и что видела модель."""

    id: UUID
    status: SimulationStatus = Field(description="Текущий статус симуляции.", examples=["completed"])
    error_code: str | None = Field(
        description="Код сбоя; null, если симуляция не упала.", examples=["LLM_TIMEOUT"]
    )
    model: str | None = Field(description="Модель LLM, которая провела ревью; null до анализа.")
    tokens_used: int | None = Field(description="Токенов LLM потрачено на оба прохода; null до анализа.")
    verification: VerificationOutcome | None = Field(
        description="Итог второго прохода модели (Reflection Pass); null, пока анализ не завершён.",
        examples=["verified"],
    )
    findings: list[SimulationFindingResponse] = Field(
        description="Замечания, прошедшие отсев и верификатор, - то, что опубликовало бы ревью, по порядку ответа модели."
    )
    discarded: list[DiscardedFindingResponse] = Field(
        description="Замечания модели, отброшенные отсевом или верификатором, с причиной."
    )
    context: list[FileContextResponse] = Field(
        description="Что модель видела по каждому файлу входа; пустой, пока контекст не собран."
    )
    created_at: UtcDatetime = Field(description="Когда симуляция запущена.")
    updated_at: UtcDatetime = Field(description="Когда симуляция последний раз менялась.")
    expires_at: UtcDatetime = Field(
        description="После этого момента симуляция и её вход удаляются, GET отвечает 404 SIMULATION_NOT_FOUND."
    )


@router.post(
    "/rules/parse",
    response_model=RulesParseResponse,
    summary="Разобрать текст правил",
    response_description="Принятые правила и отклонённые разделы.",
    description=_PARSE_DESCRIPTION,
    responses={**ERRORS_403_ORIGIN, **ERRORS_413_BODY, **ERRORS_422, **ERRORS_501_NOT_IMPLEMENTED},
)
def parse_rules(body: RulesParseRequest) -> RulesParseResponse:
    raise ApiError(HTTPStatus.NOT_IMPLEMENTED, ErrorCode.NOT_IMPLEMENTED, "Разбор правил ещё не реализован.")


@router.post(
    "/simulations",
    status_code=HTTPStatus.ACCEPTED,
    response_model=SimulationResponse,
    summary="Запустить симуляцию ревью",
    response_description="Симуляция создана и поставлена в очередь.",
    description=_CREATE_DESCRIPTION,
    responses={
        **ERRORS_403_ORIGIN,
        **ERRORS_413_BODY,
        **ERRORS_422,
        **ERRORS_429_RATE_LIMITED,
        **ERRORS_501_NOT_IMPLEMENTED,
    },
)
def create_simulation(body: SimulationRequest) -> SimulationResponse:
    raise ApiError(HTTPStatus.NOT_IMPLEMENTED, ErrorCode.NOT_IMPLEMENTED, "Симуляция ревью ещё не реализована.")


@router.get(
    "/simulations/{simulation_id}",
    response_model=SimulationResponse,
    summary="Симуляция ревью",
    response_description="Симуляция с замечаниями и контекстом модели.",
    description=_GET_DESCRIPTION,
    responses={**ERRORS_404_SIMULATION, **ERRORS_422, **ERRORS_501_NOT_IMPLEMENTED},
)
def get_simulation(
    simulation_id: Annotated[UUID, Path(description="Идентификатор симуляции.")],
) -> SimulationResponse:
    raise ApiError(HTTPStatus.NOT_IMPLEMENTED, ErrorCode.NOT_IMPLEMENTED, "Симуляция ревью ещё не реализована.")
