"""Оркестрация симуляции песочницы - фейковые `UnitOfWork` и модель, без БД и брокера."""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from itertools import count
from typing import Any
from uuid import UUID

import pytest

from app.application.review_pipeline import run_review
from app.application.simulation_pipeline import run_simulation
from app.domain.entities import (
    Definition,
    DiffAnchor,
    FileOutline,
    MergeRequest,
    ReviewJob,
    ReviewRun,
    Simulation,
    SimulationJob,
)
from app.domain.enums import (
    DefinitionKind,
    DiffSide,
    DiscardStage,
    MergeRequestState,
    ReviewRunStatus,
    SimulationSourceKind,
    SimulationStatus,
    TriggerSource,
    VerificationOutcome,
    VerifierVerdict,
)
from app.domain.file_context import ContextOmission, FileContext
from app.domain.llm_errors import LlmTimeoutError
from app.domain.simulation import SIMULATION_STALE, new_simulation

from ..fakes.context import FakeCache, FakeExtractor
from ..fakes.llm_gateway import (
    ScriptedLlmGateway,
    finding_json,
    review_response,
    verification_response,
)
from ..fakes.unit_of_work import FakeUnitOfWork
from ..fakes.vcs_gateway import RecordingVcsGateway
from .test_review_pipeline import LIMITS, LLM_LIMITS, TEXTS

NOW = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
PATH = "app/billing.py"
CODE = "".join(f"line_{n} = {n}\n" for n in range(1, 13))
RULES = """\
## Error Handling Standards

- pattern: `app/**/*.py`

1. Отказ возвращается значением Result, а не исключением
"""
DIFF = (
    "diff --git a/app/a.py b/app/a.py\n--- a/app/a.py\n+++ b/app/a.py\n@@ -1 +1 @@\n-x = 1\n+x = 2\n"
    "diff --git a/uv.lock b/uv.lock\n--- a/uv.lock\n+++ b/uv.lock\n@@ -1 +1 @@\n-a\n+b\n"
)
JOB = SimulationJob(id=UUID(int=50), simulation_id=UUID(int=1))


def _simulation(
    *, kind: SimulationSourceKind = SimulationSourceKind.CODE, source: str = CODE, rules: str = RULES
) -> Simulation:
    return new_simulation(
        simulation_id=JOB.simulation_id,
        user_id=UUID(int=7),
        source_kind=kind,
        source=source,
        rules=rules,
        file_path=PATH,
        now=NOW,
        ttl=timedelta(days=1),
    )


@dataclass
class _Tracer:
    stages: list[tuple[SimulationStatus, str | None]] = field(default_factory=list)

    def start(self, simulation_id: UUID, stage: SimulationStatus) -> Any:
        stages = self.stages

        class _Span:
            def end(self, error_code: str | None) -> None:
                stages.append((stage, error_code))

        return _Span()


def _new_id() -> Callable[[], UUID]:
    counter = count(100)
    return lambda: UUID(int=next(counter))


def _clock() -> Callable[[], datetime]:
    reads = count(1)
    return lambda: NOW + timedelta(seconds=next(reads))


def _run(uow: FakeUnitOfWork, gateway: object, **overrides: object) -> None:
    kwargs: dict[str, object] = {
        "uow": uow,
        "llm_gateway": gateway,
        "definition_extractor": FakeExtractor(),
        "context_limits": LIMITS,
        "llm_limits": LLM_LIMITS,
        "prompt_texts": TEXTS,
        "now": _clock(),
        "new_id": _new_id(),
    }
    kwargs.update(overrides)
    run_simulation(JOB, **kwargs)  # type: ignore[arg-type]


def _seeded(simulation: Simulation | None = None) -> FakeUnitOfWork:
    uow = FakeUnitOfWork()
    uow.simulations.add(simulation or _simulation())
    return uow


def _stored(uow: FakeUnitOfWork) -> Simulation:
    stored = uow.simulations.get(JOB.simulation_id)
    assert stored is not None
    return stored


def _context(uow: FakeUnitOfWork) -> tuple[FileContext, ...]:
    result = _stored(uow).result
    assert result is not None
    return result.context


def _messages(gateway: ScriptedLlmGateway, index: int = 0) -> tuple[str, str, str]:
    """Системное сообщение, правила и код запроса ревью."""
    system, rules, code = (message.content for message in gateway.requests[index].messages)
    return system, rules, code


def test_a_fragment_is_reviewed_as_a_new_file_and_the_input_is_erased() -> None:
    uow = _seeded()
    gateway = ScriptedLlmGateway(
        [
            review_response(finding_json(PATH, 7, "Магическое число"), tokens=30, model="glm-5.3"),
            verification_response(("F1", "keep"), tokens=5),
        ]
    )
    tracer = _Tracer()

    _run(uow, gateway, tracer=tracer)

    done = _stored(uow)
    assert done.status is SimulationStatus.COMPLETED
    assert (done.source, done.rules) == (None, None)
    assert (done.model, done.tokens_used, done.verification) == ("glm-5.3", 35, VerificationOutcome.VERIFIED)
    assert done.result is not None
    (finding,) = done.result.findings
    assert finding.anchor == DiffAnchor(PATH, DiffSide.NEW, new_line=7)
    assert finding.verdict is not None and finding.verdict.verdict is VerifierVerdict.KEEP
    (context,) = done.result.context
    assert context.hunks[0].splitlines()[0] == "@@ -0,0 +1,12 @@"
    assert tracer.stages == [
        (SimulationStatus.BUILDING_CONTEXT, None),
        (SimulationStatus.ANALYSING, None),
        (SimulationStatus.VERIFYING, None),
    ]


def test_the_package_has_no_repository_pull_request_or_pr_context() -> None:
    gateway = ScriptedLlmGateway([review_response()])

    _run(_seeded(), gateway)

    system, _, code = _messages(gateway)
    assert "Номер PR" not in system and "base_sha" not in system and "head_sha" not in system
    assert "Репозиторий" not in code
    assert "### Контекст PR" not in code
    # Контекста PR нет, а не «недоступен»: в «Не вошло» о нём ни слова.
    assert "pr_metadata" not in code and "commit_messages" not in code


def test_same_input_gives_the_same_findings_as_the_review_of_a_pull_request() -> None:
    """Сценарий спеки: фрагмент и правила совпадают с новым файлом и rules.md PR."""

    def answers() -> list[Any]:
        return [
            review_response(
                finding_json(PATH, 7, "Исключение вместо Result", rule=("Error Handling Standards", 1)),
                finding_json(PATH, 40, "Вне диффа"),
            ),
            verification_response(("F1", "keep")),
        ]

    review_uow = FakeUnitOfWork()
    _seed_review(review_uow)
    pr_diff = (
        f"diff --git a/{PATH} b/{PATH}\nnew file mode 100644\n--- /dev/null\n+++ b/{PATH}\n"
        "@@ -0,0 +1,12 @@\n" + "".join(f"+{line}\n" for line in CODE.splitlines())
    )
    vcs = RecordingVcsGateway(
        diff=pr_diff,
        files={(".review/rules.md", REVIEW_JOB.base_sha): RULES, (PATH, REVIEW_JOB.head_sha): CODE},
    )
    review_gateway = ScriptedLlmGateway(answers())
    run_review(
        REVIEW_JOB,
        uow=review_uow,
        llm_gateway=review_gateway,
        vcs_gateway=vcs,
        definition_extractor=FakeExtractor(outlines={CODE: OUTLINE}),
        cache_store=FakeCache(),
        context_limits=LIMITS,
        llm_limits=LLM_LIMITS,
        prompt_texts=TEXTS,
        now=_clock(),
        monotonic=lambda: 0.0,
        new_id=_new_id(),
    )
    simulation_uow = _seeded()
    simulation_gateway = ScriptedLlmGateway(answers())
    _run(simulation_uow, simulation_gateway, definition_extractor=FakeExtractor(outlines={CODE: OUTLINE}))

    reviewed = [
        (f.anchor, f.category, f.severity, f.message, f.rule)
        for f in review_uow.findings.list_for_run(REVIEW_JOB.review_run_id)
    ]
    result = _stored(simulation_uow).result
    assert result is not None
    simulated = [(f.anchor, f.category, f.severity, f.message, f.rule) for f in result.findings]
    assert len(simulated) == 1
    assert simulated == reviewed
    # Модель видела те же правила, дифф и AST; разница - только заголовок пакета и контекст PR.
    _, review_rules, review_code = _messages(review_gateway)
    _, simulation_rules, simulation_code = _messages(simulation_gateway)
    assert simulation_rules == review_rules
    changes = "### Изменения"
    assert simulation_code[simulation_code.index(changes) :] == review_code[review_code.index(changes) :]


def test_a_diff_keeps_only_reviewable_files_and_has_no_source_for_ast() -> None:
    uow = _seeded(_simulation(kind=SimulationSourceKind.DIFF, source=DIFF))
    extractor = FakeExtractor()

    _run(uow, ScriptedLlmGateway([review_response()]), definition_extractor=extractor)

    contexts = {context.file_path: context for context in _context(uow)}
    assert contexts["app/a.py"].hunks == ("@@ -1 +1 @@\n-x = 1\n+x = 2",)
    assert contexts["app/a.py"].omitted == (ContextOmission("ast", "no_source"),)
    assert contexts["uv.lock"].hunks == ()
    assert contexts["uv.lock"].omitted == (ContextOmission("file", "filtered"),)
    assert extractor.calls == []


# Класс и метод, охватывающие строку 7 фрагмента.
OUTLINE = FileOutline(
    definitions=(
        Definition(DefinitionKind.CLASS, "Billing", "class Billing:", 1, 12),
        Definition(DefinitionKind.METHOD, "charge", "def charge(self):", 5, 9, parent="Billing"),
    ),
    imports=("import os",),
)


def test_a_python_fragment_gets_its_enclosing_definitions() -> None:
    uow = _seeded()
    extractor = FakeExtractor(outlines={CODE: OUTLINE})

    _run(uow, ScriptedLlmGateway([review_response()]), definition_extractor=extractor)

    assert [source for source, _ in extractor.calls] == [CODE]
    (context,) = _context(uow)
    assert context.scopes is not None
    assert "class Billing:" in context.scopes and "def charge(self):" in context.scopes
    assert context.imports == "import os"


def test_a_fragment_above_the_ast_limit_is_not_parsed() -> None:
    uow = _seeded()
    extractor = FakeExtractor(outlines={CODE: OUTLINE})

    _run(
        uow,
        ScriptedLlmGateway([review_response()]),
        definition_extractor=extractor,
        context_limits=replace(LIMITS, ast_max_file_bytes=10),
    )

    assert extractor.calls == []
    (context,) = _context(uow)
    assert (context.scopes, context.omitted) == (None, (ContextOmission("ast", "too_large"),))


def test_a_fragment_that_breaks_the_parser_goes_without_ast() -> None:
    uow = _seeded()

    _run(uow, ScriptedLlmGateway([review_response()]), definition_extractor=FakeExtractor(error=RuntimeError("x")))

    (context,) = _context(uow)
    assert context.omitted == (ContextOmission("ast", "parse_error"),)
    assert _stored(uow).status is SimulationStatus.COMPLETED


def test_empty_rules_mean_the_standard_criteria() -> None:
    gateway = ScriptedLlmGateway([review_response()])
    _run(_seeded(_simulation(rules="")), gateway)
    _, rules, _ = _messages(gateway)
    assert "Статус загрузки правил: absent" in rules


def test_discarded_findings_show_where_and_why() -> None:
    uow = _seeded()
    gateway = ScriptedLlmGateway(
        [
            review_response(
                finding_json(PATH, 40, "Вне диффа"),
                finding_json(PATH, 2, "Чужое правило", rule=("Naming", 1)),
                finding_json(PATH, 3, "Имя переменной"),
                finding_json(PATH, 4, "Токен в коде", category="security", severity="critical"),
            ),
            verification_response(("F1", "drop"), ("F2", "drop")),
        ]
    )

    _run(uow, gateway)

    result = _stored(uow).result
    assert result is not None
    assert [(d.stage, d.candidate.message, d.reason) for d in result.discarded] == [
        (DiscardStage.SCREENING, "Вне диффа", "привязка вне показанного диффа"),
        (DiscardStage.SCREENING, "Чужое правило", "правило не показывали модели"),
        (DiscardStage.VERIFICATION, "Имя переменной", "причина F1"),
    ]
    # Ссылка на правило у отброшенного отсевом - как её дала модель.
    rule = result.discarded[1].candidate.rule
    assert rule is not None and (rule.name, rule.check) == ("Naming", 1)
    # Серьёзную находку безопасности верификатор отбросить не может: она с вердиктом `drop`.
    (kept,) = result.findings
    assert kept.message == "Токен в коде"
    assert kept.verdict is not None
    assert (kept.verdict.verdict, kept.verdict.reason) == (VerifierVerdict.DROP, "причина F2")


def test_a_verifier_failure_keeps_the_screened_findings_without_verdicts() -> None:
    uow = _seeded()
    gateway = ScriptedLlmGateway(
        [review_response(finding_json(PATH, 3, "Имя"), tokens=10), LlmTimeoutError("верификатор молчит", tokens_used=4)]
    )

    _run(uow, gateway)

    done = _stored(uow)
    assert (done.status, done.verification, done.tokens_used) == (
        SimulationStatus.COMPLETED,
        VerificationOutcome.UNAVAILABLE,
        14,
    )
    assert done.result is not None
    assert [(f.message, f.verdict) for f in done.result.findings] == [("Имя", None)]


def test_a_model_timeout_fails_the_simulation_with_its_code_and_tokens() -> None:
    uow = _seeded()
    tracer = _Tracer()

    _run(uow, ScriptedLlmGateway([LlmTimeoutError("модель молчит", tokens_used=12)]), tracer=tracer)

    failed = _stored(uow)
    assert (failed.status, failed.error_code, failed.tokens_used) == (SimulationStatus.FAILED, "LLM_TIMEOUT", 12)
    assert (failed.source, failed.rules, failed.result) == (None, None, None)
    assert tracer.stages[-1] == (SimulationStatus.ANALYSING, "LLM_TIMEOUT")


def test_a_program_error_inside_the_analysis_is_internal_error() -> None:
    uow = _seeded()

    class _Broken:
        def complete(self, request: object) -> object:
            raise KeyError("сломано")

    _run(uow, _Broken())

    failed = _stored(uow)
    assert (failed.status, failed.error_code, failed.tokens_used) == (SimulationStatus.FAILED, "INTERNAL_ERROR", None)


@pytest.mark.parametrize("status", [SimulationStatus.COMPLETED, SimulationStatus.FAILED])
def test_a_redelivered_finished_simulation_is_left_alone(status: SimulationStatus) -> None:
    finished = replace(_simulation(), status=status, source=None, rules=None, error_code="X")
    uow = _seeded(finished)
    gateway = ScriptedLlmGateway([])

    _run(uow, gateway)

    assert _stored(uow) == finished
    assert gateway.requests == []


def test_a_deleted_simulation_is_acknowledged_without_work() -> None:
    gateway = ScriptedLlmGateway([])
    _run(FakeUnitOfWork(), gateway)
    assert gateway.requests == []


def test_a_simulation_stuck_after_a_dead_worker_is_closed_as_stale() -> None:
    uow = _seeded(replace(_simulation(), status=SimulationStatus.ANALYSING))
    gateway = ScriptedLlmGateway([])

    _run(uow, gateway)

    stuck = _stored(uow)
    assert (stuck.status, stuck.error_code, stuck.source) == (SimulationStatus.FAILED, SIMULATION_STALE, None)
    assert gateway.requests == []


def test_a_simulation_closed_during_the_analysis_keeps_its_stale_outcome() -> None:
    """Проход обслуживания закрыл симуляцию, пока модель отвечала: результат отбрасывается."""
    uow = _seeded()
    scripted = ScriptedLlmGateway(
        [review_response(finding_json(PATH, 3, "Имя")), verification_response(("F1", "keep"))]
    )

    class _SweptWhileWaiting:
        def complete(self, request: Any) -> Any:
            uow.simulations.fail_stale(NOW + timedelta(hours=1), NOW + timedelta(hours=1))
            return scripted.complete(request)

    _run(uow, _SweptWhileWaiting())

    closed = _stored(uow)
    assert (closed.status, closed.error_code, closed.result) == (SimulationStatus.FAILED, SIMULATION_STALE, None)
    # Работа остановлена на первом же отказе записи: верификатор не вызывался.
    assert len(scripted.requests) == 1


def test_the_model_sees_the_secret_but_the_log_sees_no_input(caplog: pytest.LogCaptureFixture) -> None:
    secret = "ghp_" + "a1B2" * 9
    uow = _seeded(_simulation(source=f'TOKEN = "{secret}"\n'))
    gateway = ScriptedLlmGateway([LlmTimeoutError("модель молчит")])

    with caplog.at_level(logging.DEBUG):
        _run(uow, gateway)

    _, _, code = _messages(gateway)
    assert secret in code
    assert caplog.records, "сбой симуляции пишется в лог"
    assert secret not in caplog.text
    assert "Error Handling Standards" not in caplog.text
    assert PATH not in caplog.text


REVIEW_JOB = ReviewJob(
    id=UUID(int=60),
    review_run_id=UUID(int=61),
    event_type="pull_request",
    action="opened",
    installation_id=1,
    repository_provider_id="1",
    repository_full_name="owner/repo",
    pull_request_number=42,
    head_sha="1" * 40,
    base_sha="2" * 40,
)


def _seed_review(uow: FakeUnitOfWork) -> None:
    uow.merge_requests.add(
        MergeRequest(
            id=UUID(int=62),
            repository_id=UUID(int=63),
            number=42,
            title="t",
            description="",
            author="a",
            source_branch="feat",
            target_branch="develop",
            head_sha=REVIEW_JOB.head_sha,
            state=MergeRequestState.OPEN,
            created_at=NOW,
            updated_at=NOW,
        )
    )
    uow.review_runs.add(
        ReviewRun(
            id=REVIEW_JOB.review_run_id,
            merge_request_id=UUID(int=62),
            head_sha=REVIEW_JOB.head_sha,
            status=ReviewRunStatus.QUEUED,
            trigger=TriggerSource.WEBHOOK,
            last_progress_at=NOW,
            created_at=NOW,
            updated_at=NOW,
        )
    )
