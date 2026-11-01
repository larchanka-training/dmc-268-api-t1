"""Правила симуляции ревью в песочнице: запуск, переходы статуса, допуск и вход-фрагмент.

Чистые функции: время, идентификаторы и вход приходят аргументами. Переходы -
одна таблица, как у прогона ревью (`app/domain/lifecycle.py`): итоговая
симуляция больше ничего не принимает, поэтому повторная доставка задачи или
поздний итог воркера после перевода зависшей в `failed` её не трогают.
Итоговый переход стирает вход пользователя: после итога код и правила никто
не читает, а хранить их - значит хранить возможные секреты (D10).
"""

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from types import MappingProxyType
from typing import Any
from uuid import UUID

from app.domain.diff_parser import filter_diff_files, parse_diff, split_lines
from app.domain.entities import (
    DiscardedFinding,
    Finding,
    Hunk,
    LlmFinding,
    LlmRuleRef,
    ParsedFile,
    Simulation,
    SimulationFinding,
    SimulationResult,
    Verdict,
)
from app.domain.enums import (
    DiscardStage,
    SimulationSourceKind,
    SimulationStatus,
    VerificationOutcome,
)
from app.domain.file_context import file_contexts
from app.domain.result import Result
from app.domain.verification import AppliedVerdicts, RejectedFinding

S = SimulationStatus

SIMULATION_TERMINAL_STATUSES: frozenset[SimulationStatus] = frozenset({S.COMPLETED, S.FAILED})

_ALLOWED: MappingProxyType[SimulationStatus, frozenset[SimulationStatus]] = MappingProxyType(
    {
        S.QUEUED: frozenset({S.BUILDING_CONTEXT, S.FAILED}),
        S.BUILDING_CONTEXT: frozenset({S.ANALYSING, S.FAILED}),
        S.ANALYSING: frozenset({S.VERIFYING, S.FAILED}),
        S.VERIFYING: frozenset({S.COMPLETED, S.FAILED}),
        S.COMPLETED: frozenset(),
        S.FAILED: frozenset(),
    }
)

# Через сколько секунд повторить запуск, пока идёт прошлая симуляция: каденс
# опроса фронта, к этому времени он, скорее всего, уже увидит её итог.
ACTIVE_RETRY_AFTER_SECONDS = 5

# Коды сбоя симуляции сверх кодов анализа (`LLM_TIMEOUT`, `INTERNAL_ERROR` и т.д.).
# Незавершённая симуляция без перехода дольше порога зависания (проход обслуживания).
SIMULATION_STALE = "SIMULATION_STALE"
# Задача не встала в очередь: симуляция закрыта сразу, слот лимита свободен.
QUEUE_UNAVAILABLE = "QUEUE_UNAVAILABLE"

# Маркер git у последней строки без перевода строки: `parse_diff` его не считает строкой файла.
_NO_NEWLINE_MARKER = "\\ No newline at end of file"


def new_simulation(
    *,
    simulation_id: UUID,
    user_id: UUID,
    source_kind: SimulationSourceKind,
    source: str,
    rules: str,
    file_path: str | None,
    now: datetime,
    ttl: timedelta,
) -> Simulation:
    """Симуляция, запущенная в момент `now`: в очереди, удаляется через `ttl`."""
    return Simulation(
        id=simulation_id,
        user_id=user_id,
        status=S.QUEUED,
        source_kind=source_kind,
        created_at=now,
        updated_at=now,
        expires_at=now + ttl,
        file_path=file_path if source_kind is SimulationSourceKind.CODE else None,
        source=source,
        rules=rules,
    )


def is_expired(simulation: Simulation, now: datetime) -> bool:
    """Симуляция удалена по сроку начиная с `expires_at`, даже если строку ещё не убрали."""
    return now >= simulation.expires_at


def advance_simulation(
    simulation: Simulation, status: SimulationStatus, now: datetime
) -> Result[Simulation]:
    """Перевести симуляцию на следующий этап анализа или вернуть причину отказа.

    Итоговые статусы ставят `complete_simulation` и `fail_simulation`: у
    `completed` должен быть результат, у `failed` - код сбоя.
    """
    if status in SIMULATION_TERMINAL_STATUSES:
        return Result.failure(f"{status} ставят complete_simulation и fail_simulation")
    return _move(simulation, status, now)


def complete_simulation(
    simulation: Simulation,
    result: SimulationResult,
    *,
    model: str,
    tokens_used: int,
    verification: VerificationOutcome,
    now: datetime,
) -> Result[Simulation]:
    """Завершить анализ результатом; вход пользователя стирается."""
    moved = _move(simulation, S.COMPLETED, now)
    if not moved.ok:
        return moved
    return Result.success(
        replace(
            moved.unwrap(),
            result=result,
            model=model,
            tokens_used=tokens_used,
            verification=verification,
        )
    )


def fail_simulation(
    simulation: Simulation,
    error_code: str,
    now: datetime,
    *,
    model: str | None = None,
    tokens_used: int | None = None,
) -> Result[Simulation]:
    """Закрыть симуляцию сбоем с кодом и потраченными токенами; вход пользователя стирается."""
    moved = _move(simulation, S.FAILED, now)
    if not moved.ok:
        return moved
    return Result.success(
        replace(moved.unwrap(), error_code=error_code, model=model, tokens_used=tokens_used)
    )


def next_simulation_status(
    current: SimulationStatus, requested: SimulationStatus
) -> Result[SimulationStatus]:
    """Решить, может ли симуляция перейти из `current` в `requested`; ею же сверяется адаптер."""
    if current in SIMULATION_TERMINAL_STATUSES:
        return Result.failure(f"{current} is terminal; cannot move to {requested}")
    if requested not in _ALLOWED[current]:
        return Result.failure(f"cannot move from {current} to {requested}")
    return Result.success(requested)


def _move(simulation: Simulation, status: SimulationStatus, now: datetime) -> Result[Simulation]:
    verdict = next_simulation_status(simulation.status, status)
    if not verdict.ok:
        return Result.failure(verdict.error or "rejected")
    moved = replace(simulation, status=status, updated_at=now)
    if status in SIMULATION_TERMINAL_STATUSES:
        moved = replace(moved, source=None, rules=None)
    return Result.success(moved)


@dataclass(frozen=True, slots=True)
class Admitted:
    """Запуск допущен."""


@dataclass(frozen=True, slots=True)
class Limited:
    """Запуск отклонён лимитом: повторить не раньше чем через `retry_after` секунд.

    `active` - отказ из-за незавершённой симуляции, иначе - из-за лимита за окно.
    """

    retry_after: int
    active: bool


def admit_simulation(
    *,
    active: bool,
    recent_created_at: Sequence[datetime],
    now: datetime,
    max_per_window: int,
    window: timedelta,
) -> Admitted | Limited:
    """Решить, можно ли пользователю запустить ещё одну симуляцию.

    Незавершённая симуляция - отказ на `ACTIVE_RETRY_AFTER_SECONDS`. Окно
    скользящее: запуск ровно `window` назад в него уже не входит. При
    исчерпанном лимите `retry_after` - до момента, когда в окне останется на
    один запуск меньше лимита, округлённо вверх и не меньше секунды.
    """
    if max_per_window < 1:
        raise ValueError("max_per_window must be at least 1")
    if active:
        return Limited(ACTIVE_RETRY_AFTER_SECONDS, active=True)
    in_window = sorted(created_at for created_at in recent_created_at if created_at > now - window)
    if len(in_window) < max_per_window:
        return Admitted()
    frees_at = in_window[len(in_window) - max_per_window] + window
    return Limited(max(1, math.ceil((frees_at - now).total_seconds())), active=False)


def simulation_result(
    *,
    rejected: Sequence[RejectedFinding],
    kept: Sequence[Finding],
    verdicts: AppliedVerdicts | None,
    context_body: Mapping[str, Any],
) -> SimulationResult:
    """Итог анализа для показа: замечания с вердиктами, отброшенные с причиной, контекст по файлам.

    `kept` - замечания, которые сохранило бы ревью; без вердиктов (`verdicts` -
    `None`, второй проход не дал их) они идут без вердикта. Отброшенные - сначала
    отсевом, затем верификатором, каждые в порядке ответа модели.
    """
    if verdicts is None:
        findings = tuple(_simulation_finding(finding, None) for finding in kept)
        dropped: tuple[DiscardedFinding, ...] = ()
    else:
        findings = tuple(_simulation_finding(judged.finding, judged.verdict) for judged in verdicts.kept)
        dropped = tuple(
            DiscardedFinding(DiscardStage.VERIFICATION, judged.verdict.reason, _as_candidate(judged.finding))
            for judged in verdicts.dropped
        )
    screened = tuple(DiscardedFinding(DiscardStage.SCREENING, r.reason, r.candidate) for r in rejected)
    return SimulationResult(
        findings=findings,
        discarded=screened + dropped,
        context=file_contexts(context_body),
    )


def _simulation_finding(finding: Finding, verdict: Verdict | None) -> SimulationFinding:
    return SimulationFinding(
        id=finding.id,
        anchor=finding.anchor,
        category=finding.category,
        severity=finding.severity,
        message=finding.message,
        suggestion=finding.suggestion,
        confidence=finding.confidence,
        rule=finding.rule,
        verdict=verdict,
    )


def _as_candidate(finding: Finding) -> LlmFinding:
    """Замечание после отсева в форме ответа модели: ссылка на правило - именем и номером."""
    return LlmFinding(
        anchor=finding.anchor,
        category=finding.category,
        severity=finding.severity,
        message=finding.message,
        suggestion=finding.suggestion,
        confidence=finding.confidence,
        rule=None if finding.rule is None else LlmRuleRef(finding.rule.name, finding.rule.check),
    )


def simulation_files(
    source_kind: SimulationSourceKind, source: str, file_path: str | None
) -> list[ParsedFile]:
    """Файлы входа симуляции так, как их увидел бы разбор диффа ревью, до фильтра."""
    if source_kind is SimulationSourceKind.CODE:
        if file_path is None:
            raise ValueError("a code fragment needs a file path")
        return [code_as_new_file(file_path, source)]
    return parse_diff(source)


def has_reviewable_hunks(files: Sequence[ParsedFile]) -> bool:
    """Останется ли модели что ревьюить после того же фильтра, что у ревью."""
    return any(parsed.hunks for parsed in filter_diff_files(files))


def code_as_new_file(file_path: str, content: str) -> ParsedFile:
    """Фрагмент кода как новый файл диффа: один hunk, все строки добавлены.

    Тот же `ParsedFile`, что `parse_diff` даёт для нового файла с этим текстом в
    запросе на изменение: строки делятся только по `\\n`, `\\r` перед ним
    отбрасывается, последняя строка без перевода строки несёт маркер git, а
    счётчик `1` в заголовке опускается. Пустой фрагмент - файл без hunk'ов.
    """
    if not content:
        return ParsedFile(file_path=file_path)
    lines = split_lines(content)
    count = len(lines)
    header = "@@ -0,0 +1 @@" if count == 1 else f"@@ -0,0 +1,{count} @@"
    body = [f"+{line}" for line in lines]
    if not content.endswith("\n"):
        body.append(_NO_NEWLINE_MARKER)
    hunk = Hunk(
        file_path=file_path,
        new_start=1,
        new_count=count,
        changed_new_lines=frozenset(range(1, count + 1)),
        text="\n".join([header, *body]),
    )
    return ParsedFile(file_path=file_path, hunks=(hunk,))
