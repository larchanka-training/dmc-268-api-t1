"""Песочница правил: разбор `.review/rules.md` и симуляция ревью на своём коде.

Пользователь вставляет фрагмент кода или дифф и текст правил и видит, что скажет ревью,
без запроса на изменение и без публикации (#288). Запуск записывает симуляцию и ставит её
в общую очередь ниже ревью; анализ делает воркер, итог фронтенд читает опросом `GET`.
"""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from http import HTTPStatus
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Path
from pydantic import AfterValidator, BaseModel, Field, StringConstraints

from app.api.dependencies import get_container, get_job_queue, get_unit_of_work_factory
from app.api.errors import (
    ERRORS_403_ORIGIN,
    ERRORS_404_SIMULATION,
    ERRORS_413_BODY,
    ERRORS_422,
    ERRORS_429_RATE_LIMITED,
    ApiError,
    RateLimited,
)
from app.api.problems import ErrorCode
from app.api.schemas import (
    ContextOmissionResponse,
    FileContextResponse,
    FindingBase,
    RuleRefResponse,
    ShownRuleResponse,
    StrictRequest,
    UtcDatetime,
    VerifierVerdictResponse,
    without_nul,
)
from app.api.session import SESSION_REQUIRED, SESSION_RESPONSES, current_user
from app.application.ports import UnitOfWork
from app.application.ports.job_queue import JobQueue
from app.application.use_cases.start_simulation import (
    NothingToReview,
    SimulationLimits,
    SimulationRequestInput,
    start_simulation,
)
from app.domain.entities import DiscardedFinding, Simulation, SimulationFinding, User
from app.domain.enums import (
    DiffSide,
    DiscardStage,
    FindingCategory,
    FindingSeverity,
    SimulationSourceKind,
    SimulationStatus,
    VerificationOutcome,
)
from app.domain.file_context import FileContext
from app.domain.ids import new_id
from app.domain.repo_rules import RULES_PATH, matching_files, outline_rules_markdown
from app.domain.simulation import Limited, is_expired
from app.infrastructure.container import Container

# Потолки входа в символах - для схемы. Правила, кроме того, не больше лимита в байтах UTF-8,
# с которым ревью читает `.review/rules.md` (`RULES_MAX_BYTES`, по умолчанию 32 КиБ), - его
# проверяет ручка: лимит из конфигурации. Код и дифф - не больше пакета контекста модели
# (`CONTEXT_MAX_TOKENS` × 4 байта, 128 КиБ): больше модель всё равно не увидит.
RULES_TEXT_MAX_LENGTH = 32_768
SOURCE_MAX_LENGTH = 131_072
FILE_PATH_MAX_LENGTH = 1_024
PARSE_PATHS_MAX_ITEMS = 100

_PARSE_DESCRIPTION = (
    "Требует сессию и Origin сервиса; Origin проверяется раньше сессии и разбора тела. Разбирает текст "
    "тем же парсером, что ревью запроса на изменение, без вызова модели, - для подсказок в редакторе "
    "песочницы: что из текста станет правилами ревью. Принятые правила - по порядку в тексте, со строкой "
    "заголовка, пунктами и файлами из paths, к которым правило применимо; отклонённые разделы - с "
    "причиной и строкой заголовка. Строки нумеруются с 1 и делятся только по переводу строки, как в git. "
    "Текст больше лимита, с которым ревью читает .review/rules.md (RULES_MAX_BYTES, по умолчанию 32768 "
    "байт в UTF-8), - 422 в body.rules: такой файл ревью не разбирает вовсе. Пустой текст - 200 без "
    "правил: ревью шло бы по стандартным критериям."
)
_CREATE_DESCRIPTION = (
    "Требует сессию и Origin сервиса; Origin проверяется раньше сессии и разбора тела. 202 с симуляцией в "
    "статусе queued. Воркер прогонит вход тем же конвейером, что ревью запроса на изменение, - сборка "
    "контекста, ревью, отсев, верификатор, - без провайдера VCS и без публикации. Фрагмент кода "
    "(source.kind = code) ревьюится как новый файл, все строки - добавленные; file_path нужен, чтобы "
    "сработали pattern правил и выбрался язык AST. Дифф (source.kind = diff) берёт файлы и пути из своих "
    "заголовков. Вход, в котором после разбора и того же фильтра, что у ревью (lock-файлы, сгенерированное, "
    "бинарное), нет ни одного hunk'а, - 422 в body.source; правила больше лимита, с которым ревью читает "
    ".review/rules.md (RULES_MAX_BYTES), - 422 в body.rules. Пустой rules - ревью по стандартным "
    "критериям. Ход и итог клиент читает GET /simulations/{simulation_id}, опрашивая раз в 1-2 секунды, "
    "пока статус не completed или failed: в общий поток GET /events симуляции не попадают. Задача не "
    "встала в очередь - всё равно 202, симуляция сразу failed с error_code QUEUE_UNAVAILABLE. Каждый запуск "
    "вызывает модель, поэтому запуски ограничены на пользователя - одна незавершённая симуляция "
    "(Retry-After: 5, каденс опроса) и SIMULATION_MAX_PER_HOUR за скользящий час (Retry-After - до выхода "
    "самого старого запуска из окна): 429 RATE_LIMITED."
)
_GET_DESCRIPTION = (
    "Требует сессию. Симуляция, которую запустил этот пользователь, - статус, замечания, отброшенные "
    "замечания и контекст модели; контекст хранится с секретами, заменёнными меткой. Код и правила "
    "запуска ответ не отдаёт: они стираются с итогом. Чужая, несуществующая и удалённая по сроку "
    "хранения (expires_at) симуляция - 404 SIMULATION_NOT_FOUND без различия."
)
_NOTHING_TO_REVIEW = (
    "после разбора и фильтра ревью ревьюить нечего: нет ни одного hunk'а - дифф не в формате git diff "
    "(у каждого файла строка diff --git), только переименования, lock-файлы или сгенерированные файлы"
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
    responses={**ERRORS_403_ORIGIN, **ERRORS_413_BODY, **ERRORS_422},
)
def parse_rules(body: RulesParseRequest, container: Container = Depends(get_container)) -> RulesParseResponse:
    _check_rules_size(body.rules, container.settings.rules_max_bytes)
    accepted, rejected = outline_rules_markdown(body.rules)
    return RulesParseResponse(
        rules=[
            ParsedRuleResponse(
                name=outline.rule.name,
                line=outline.line,
                patterns=list(outline.rule.patterns),
                excludes=list(outline.rule.excludes),
                checks=[
                    ParsedCheckResponse(number=number, text=text, line=line)
                    for number, (text, line) in enumerate(zip(outline.rule.checks, outline.check_lines, strict=True), start=1)
                ],
                matched_paths=list(matching_files(outline.rule, body.paths)),
            )
            for outline in accepted
        ],
        rejected=[
            ParsedRejectedRuleResponse(name=outline.rejected.name, line=outline.line, reason=outline.rejected.reason)
            for outline in rejected
        ],
    )


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
    },
)
def create_simulation(
    body: SimulationRequest,
    user: User = Depends(current_user),
    container: Container = Depends(get_container),
    uow_factory: Callable[[], UnitOfWork] = Depends(get_unit_of_work_factory),
    queue: JobQueue = Depends(get_job_queue),
) -> SimulationResponse:
    settings = container.settings
    _check_rules_size(body.rules, settings.rules_max_bytes)
    source = body.source
    request = (
        SimulationRequestInput(SimulationSourceKind.CODE, source.content, body.rules, source.file_path)
        if isinstance(source, CodeSource)
        else SimulationRequestInput(SimulationSourceKind.DIFF, source.diff, body.rules)
    )
    outcome = start_simulation(
        request,
        user_id=user.id,
        uow=uow_factory(),
        queue=queue,
        limits=SimulationLimits(
            max_per_window=settings.simulation_max_per_hour,
            ttl=timedelta(seconds=settings.simulation_ttl_seconds),
        ),
        now=datetime.now(UTC),
        new_id=new_id,
    )
    if isinstance(outcome, NothingToReview):
        raise _invalid("body.source", _NOTHING_TO_REVIEW)
    if isinstance(outcome, Limited):
        detail = (
            "Предыдущая симуляция ещё идёт: дождитесь её итога."
            if outcome.active
            else f"Лимит запусков симуляции за час исчерпан: {settings.simulation_max_per_hour}."
        )
        raise RateLimited(detail, outcome.retry_after)
    return _simulation_response(outcome)


@router.get(
    "/simulations/{simulation_id}",
    response_model=SimulationResponse,
    summary="Симуляция ревью",
    response_description="Симуляция с замечаниями и контекстом модели.",
    description=_GET_DESCRIPTION,
    responses={**ERRORS_404_SIMULATION, **ERRORS_422},
)
def get_simulation(
    simulation_id: Annotated[UUID, Path(description="Идентификатор симуляции.")],
    user: User = Depends(current_user),
    uow_factory: Callable[[], UnitOfWork] = Depends(get_unit_of_work_factory),
) -> SimulationResponse:
    with uow_factory() as uow:
        simulation = uow.simulations.get_for_user(simulation_id, user.id)
    # Истёкшую до прохода обслуживания строку не отдаём: срок - тот, что обещан в ответе.
    if simulation is None or is_expired(simulation, datetime.now(UTC)):
        raise ApiError(HTTPStatus.NOT_FOUND, ErrorCode.SIMULATION_NOT_FOUND, "Симуляция не найдена.")
    return _simulation_response(simulation)


def _invalid(where: str, reason: str) -> ApiError:
    """`422`, который ставит ручка: проверке нужна конфигурация или разбор, а не только модель тела."""
    return ApiError(
        HTTPStatus.UNPROCESSABLE_CONTENT,
        ErrorCode.VALIDATION_ERROR,
        "Параметры запроса не прошли проверку, ошибок: 1.",
        errors=[{"where": where, "reason": reason}],
    )


def _check_rules_size(rules: str, limit: int) -> None:
    """Больший текст ревью не разбирает вовсе - тот же лимит у разбора и у симуляции."""
    if len(rules.encode("utf-8")) > limit:
        raise _invalid("body.rules", f"размер больше {limit} байт в UTF-8")


def _simulation_response(simulation: Simulation) -> SimulationResponse:
    result = simulation.result
    return SimulationResponse(
        id=simulation.id,
        status=simulation.status,
        error_code=simulation.error_code,
        model=simulation.model,
        tokens_used=simulation.tokens_used,
        verification=simulation.verification,
        findings=[_finding_response(f) for f in result.findings] if result else [],
        discarded=[_discarded_response(d) for d in result.discarded] if result else [],
        context=[_context_response(c) for c in result.context] if result else [],
        created_at=simulation.created_at,
        updated_at=simulation.updated_at,
        expires_at=simulation.expires_at,
    )


def _finding_response(finding: SimulationFinding) -> SimulationFindingResponse:
    rule = finding.rule
    verdict = finding.verdict
    return SimulationFindingResponse(
        id=finding.id,
        file_path=finding.anchor.file_path,
        side=finding.anchor.side,
        old_line=finding.anchor.old_line,
        new_line=finding.anchor.new_line,
        category=finding.category,
        severity=finding.severity,
        message=finding.message,
        suggestion=finding.suggestion,
        confidence=finding.confidence,
        rule=None if rule is None else RuleRefResponse(name=rule.name, check=rule.check, check_text=rule.check_text),
        verdict=None if verdict is None else VerifierVerdictResponse(verdict=verdict.verdict, reason=verdict.reason),
    )


def _discarded_response(discarded: DiscardedFinding) -> DiscardedFindingResponse:
    candidate = discarded.candidate
    rule = candidate.rule
    return DiscardedFindingResponse(
        stage=discarded.stage,
        reason=discarded.reason,
        file_path=candidate.anchor.file_path,
        side=candidate.anchor.side,
        # Строку модель даёт всегда; у привязки без строки (не бывает от модели) - 0.
        line=candidate.anchor.line or 0,
        category=candidate.category,
        severity=candidate.severity,
        message=candidate.message,
        rule=None if rule is None else RuleMentionResponse(name=rule.name, check=rule.check),
    )


def _context_response(context: FileContext) -> FileContextResponse:
    return FileContextResponse(
        file_path=context.file_path,
        hunks=list(context.hunks),
        scopes=context.scopes,
        imports=context.imports,
        rules=[ShownRuleResponse(name=rule.name, checks=list(rule.checks)) for rule in context.rules],
        omitted=[ContextOmissionResponse(part=o.part, reason=o.reason) for o in context.omitted],
    )
