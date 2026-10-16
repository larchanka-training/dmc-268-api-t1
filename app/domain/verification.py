"""Что делается с ответом модели до сохранения замечаний.

Два шага, оба - чистые функции (спека `review-verification`):

- `screen_findings` - детерминированный отсев до верификатора: привязка
  внутри показанных hunk'ов, ссылка на правило, которое модель видела и
  которое применимо к файлу, без префикса атрибуции в тексте, без дублей.
  Отклонённое замечание отклоняется целиком: ссылка на правило, которого
  модель не видела, - типичный след «правила», подложенного в дифф.
- `apply_verdicts` - вердикты верификатора. Возвращает подмножество исходных
  объектов, поэтому изменить поле замечания верификатору нечем.

Идентификаторы и время приходят аргументами: тест сверяет точные значения.
"""

import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.domain.dedup import deduplicate
from app.domain.diff import validate_anchor
from app.domain.entities import (
    Finding,
    Hunk,
    LlmFinding,
    LlmRuleRef,
    RuleRef,
    ShownRule,
    Verdict,
)
from app.domain.enums import VerifierVerdict
from app.domain.prompt import candidate_ids
from app.domain.result import Result

# Префикс атрибуции, который модель всё же написала сама. Атрибуцию строит
# сервис из проверенной ссылки, поэтому в тексте замечания её быть не должно:
# иначе замечание без ссылки выглядело бы как замечание по правилу. Скобки
# допускают один уровень вложенности: «(пункт 1: текст (уточнение))».
_MODEL_PREFIX = re.compile(
    r"""
    \A\s*согласно\s+пользовательскому\s+правилу\s*
    [«"„'][^\n»"“']*[»"“']                          # имя правила в кавычках
    (?:\s*\((?:[^()\n]|\([^()\n]*\))*\))?           # пересказ в скобках
    \s*:\s*
    """,
    re.IGNORECASE | re.VERBOSE,
)


@dataclass(frozen=True, slots=True)
class Screening:
    """Итог отсева: принятые замечания и сколько отклонено."""

    kept: tuple[Finding, ...]
    rejected: int


def screen_findings(
    candidates: Sequence[LlmFinding],
    *,
    hunks: Iterable[Hunk],
    shown_rules: Sequence[ShownRule],
    review_run_id: UUID,
    now: datetime,
    new_id: Callable[[], UUID],
) -> Screening:
    hunk_list = list(hunks)
    rules = {rule.name: rule for rule in shown_rules}
    accepted: list[Finding] = []
    rejected = 0
    for candidate in candidates:
        anchor = validate_anchor(candidate.anchor, hunk_list)
        rule = _resolve(candidate.rule, candidate.anchor.file_path, rules)
        message = _MODEL_PREFIX.sub("", candidate.message, count=1).strip()
        if not anchor.ok or not rule.ok or not message:
            rejected += 1
            continue
        accepted.append(
            Finding(
                id=new_id(),
                review_run_id=review_run_id,
                anchor=anchor.unwrap(),
                category=candidate.category,
                severity=candidate.severity,
                message=message,
                created_at=now,
                updated_at=now,
                suggestion=candidate.suggestion,
                confidence=candidate.confidence,
                rule=rule.unwrap(),
            )
        )
    kept, duplicates = deduplicate(accepted)
    return Screening(kept=tuple(kept), rejected=rejected + len(duplicates))


def _resolve(
    reference: LlmRuleRef | None, file_path: str, rules: Mapping[str, ShownRule]
) -> Result[RuleRef | None]:
    """Сверить ссылку с показанными правилами; без ссылки - успех с `None`."""
    if reference is None:
        return Result.success(None)
    rule = rules.get(reference.name.strip())
    if rule is None:
        return Result.failure(f"правило {reference.name!r} модели не показывали")
    if file_path not in rule.files:
        return Result.failure(f"правило {rule.name!r} не применимо к {file_path}")
    if not 1 <= reference.check <= len(rule.checks):
        return Result.failure(f"у правила {rule.name!r} нет пункта {reference.check}")
    return Result.success(
        RuleRef(name=rule.name, check=reference.check, check_text=rule.checks[reference.check - 1])
    )


def apply_verdicts(
    candidates: Sequence[Finding], verdicts: Mapping[str, Verdict]
) -> tuple[tuple[Finding, ...], int]:
    """Оставить замечания с вердиктом `keep`; вернуть их и число отброшенных.

    Полноту вердиктов проверяет `check_verdicts` до вызова: здесь у каждого
    кандидата вердикт есть.
    """
    kept = tuple(
        finding
        for candidate_id, finding in zip(candidate_ids(len(candidates)), candidates, strict=True)
        if verdicts[candidate_id].verdict is VerifierVerdict.KEEP
    )
    return kept, len(candidates) - len(kept)
