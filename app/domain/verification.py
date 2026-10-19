"""Что делается с ответом модели до сохранения замечаний.

Два шага, оба - чистые функции (спека `review-verification`):

- `screen_findings` - детерминированный отсев до верификатора: путь в том
  виде, в каком его видела модель, сводится к пути из диффа, привязка
  внутри показанных hunk'ов, ссылка на правило, которое модель видела и
  которое применимо к файлу, без префикса атрибуции в тексте, без дублей.
  Отклонённое замечание отклоняется целиком: ссылка на правило, которого
  модель не видела, - типичный след «правила», подложенного в дифф.
- `apply_verdicts` - вердикты верификатора. Возвращает подмножество исходных
  объектов, поэтому изменить поле замечания верификатору нечем, а серьёзную
  находку безопасности он отбросить не может.

Идентификаторы и время приходят аргументами: тест сверяет точные значения.
"""

import re
import unicodedata
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
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
from app.domain.enums import FindingCategory, FindingSeverity, VerifierVerdict
from app.domain.prompt import candidate_ids
from app.domain.result import Result
from app.domain.untrusted import sanitize

# Префикс атрибуции, который модель всё же написала сама, ищется не в тексте, а
# в его скелете (`_skeleton`): иначе его прячут символ нулевой ширины, латинская
# `o` в «Согласно» или `**` вокруг. Атрибуцию строит сервис из проверенной
# ссылки, поэтому в тексте замечания её быть не должно: иначе замечание без
# ссылки выглядело бы как замечание по правилу.
#
# Разметка Markdown, которая может стоять до префикса и вокруг его частей.
_MARKUP_CHARS = "*_>#`~"
_MARKUP = rf"[\s{re.escape(_MARKUP_CHARS)}]*"
# Голова префикса: слова и имя правила в кавычках. Имя - до закрывающей кавычки
# своей пары, за которой идёт пересказ в скобках или разделитель: апостроф
# внутри имени его не обрывает. Скобки пересказа регулярным выражением не
# разобрать (вложенность любая), их считает `_prefix_end`.
_PREFIX_HEAD = re.compile(
    rf"""
    {_MARKUP}согласно\s*пользовательскому\s*правилу\s*
    (?:«.*?»|".*?"|„.*?[“”]|“.*?”|'.*?')
    (?={_MARKUP}[(:\-\u2013\u2014])
    """,
    re.VERBOSE,
)
# Двоеточие, дефис, короткое и длинное тире.
_SEPARATORS = (":", "-", "\u2013", "\u2014")
# Начало текста, которое читается как префикс, даже если снять его не вышло:
# без имени в кавычках, без разделителя. Такое замечание не публикуется.
_ATTRIBUTION_START = re.compile(rf"{_MARKUP}согласно\s*пользовательск\w*\s*правил")
# Латинские и греческие буквы (после `casefold`), которые на глаз не отличить
# от кириллических в словах префикса.
_LOOKALIKES = str.maketrans(
    {
        "a": "а", "b": "в", "c": "с", "e": "е", "h": "н", "k": "к", "m": "м",
        "o": "о", "p": "р", "t": "т", "x": "х", "y": "у",
        "α": "а", "β": "в", "ε": "е", "η": "н", "κ": "к", "μ": "м",
        "ο": "о", "ρ": "р", "τ": "т", "χ": "х", "υ": "у",
    }
)  # fmt: skip

# Цель атаки через дифф - подавить именно такую находку, а верификатор читает
# тот же недоверенный контекст: отбросить её он не может (`review-verification`).
_UNDROPPABLE_SECURITY = frozenset({FindingSeverity.HIGH, FindingSeverity.CRITICAL})


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
    # Модель видела пути после `sanitize` и ссылается на них в том виде, а
    # находка хранит путь из диффа: по нему публикуется комментарий и с ним
    # сверяются файлы правила. Сырой путь тоже принимается и при совпадении
    # главнее.
    paths = {sanitize(h.file_path): h.file_path for h in hunk_list} | {
        h.file_path: h.file_path for h in hunk_list
    }
    rules = {rule.name: rule for rule in shown_rules}
    accepted: list[Finding] = []
    rejected = 0
    for candidate in candidates:
        path = paths.get(candidate.anchor.file_path, candidate.anchor.file_path)
        anchor = validate_anchor(replace(candidate.anchor, file_path=path), hunk_list)
        rule = _resolve(candidate.rule, path, rules)
        message = (_without_model_prefix(candidate.message) or "").strip()
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


def _without_model_prefix(message: str) -> str | None:
    """Текст замечания без префиксов атрибуции; `None`, если префикс не снять.

    Префикс снимается, пока он есть: второй, спрятанный за первым, иначе
    опубликовался бы с атрибуцией чужого правила. Текст без префикса - как есть.
    """
    rest = message
    while (end := _prefix_end(rest)) is not None:
        rest = rest[end:]
    skeleton, _ = _skeleton(rest)
    return None if _ATTRIBUTION_START.match(skeleton) else rest


def _prefix_end(text: str) -> int | None:
    """Индекс в `text` сразу за префиксом атрибуции в его начале; нет префикса - `None`."""
    skeleton, origins = _skeleton(text)
    head = _PREFIX_HEAD.match(skeleton)
    if head is None:
        return None
    position = _after_markup(skeleton, head.end())
    if skeleton.startswith("(", position):
        close = _closing_parenthesis(skeleton[position:])
        if close is None:
            return None
        position = _after_markup(skeleton, position + close + 1)
    if not skeleton.startswith(_SEPARATORS, position):
        return None
    # Режется исходный текст: сразу за символом, давшим разделитель.
    return origins[position] + 1


def _after_markup(skeleton: str, position: int) -> int:
    """Первая позиция с `position`, где не пробел и не разметка Markdown."""
    while position < len(skeleton) and (
        skeleton[position].isspace() or skeleton[position] in _MARKUP_CHARS
    ):
        position += 1
    return position


def _skeleton(text: str) -> tuple[str, tuple[int, ...]]:
    """Текст для поиска префикса и индекс исходного символа для каждого символа скелета.

    Символы `Cf` (zero-width, bidi) выбрасываются, остальные проходят NFKC,
    `casefold` и замену похожих букв на кириллические. Поиск идёт по скелету,
    а режется исходный текст: в замечание попадают слова модели как есть.
    """
    chars: list[str] = []
    origins: list[int] = []
    for index, char in enumerate(text):
        if unicodedata.category(char) == "Cf":
            continue
        for folded in unicodedata.normalize("NFKC", char).casefold().translate(_LOOKALIKES):
            chars.append(folded)
            origins.append(index)
    return "".join(chars), tuple(origins)


def _closing_parenthesis(text: str) -> int | None:
    """Индекс скобки, закрывающей открытую в начале `text`; в пределах строки."""
    depth = 0
    for index, char in enumerate(text):
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return index
        elif char == "\n":
            return None
    return None


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


@dataclass(frozen=True, slots=True)
class AppliedVerdicts:
    """Итог вердиктов: что сохранить и что с какой причиной верификатор хотел отбросить.

    `overruled` - замечания, которые верификатор отбросить не может: они есть
    и в `kept`, причина его `drop` нужна логу прогона.
    """

    kept: tuple[Finding, ...]
    dropped: tuple[tuple[Finding, str], ...]
    overruled: tuple[tuple[Finding, str], ...]


def apply_verdicts(candidates: Sequence[Finding], verdicts: Mapping[str, Verdict]) -> AppliedVerdicts:
    """Оставить замечания с вердиктом `keep` и те, что отбрасывать нельзя.

    Полноту вердиктов проверяет `check_verdicts` до вызова: здесь у каждого
    кандидата вердикт есть.
    """
    kept: list[Finding] = []
    dropped: list[tuple[Finding, str]] = []
    overruled: list[tuple[Finding, str]] = []
    for candidate_id, finding in zip(candidate_ids(len(candidates)), candidates, strict=True):
        verdict = verdicts[candidate_id]
        if verdict.verdict is VerifierVerdict.KEEP:
            kept.append(finding)
        elif _droppable(finding):
            dropped.append((finding, verdict.reason))
        else:
            kept.append(finding)
            overruled.append((finding, verdict.reason))
    return AppliedVerdicts(tuple(kept), tuple(dropped), tuple(overruled))


def _droppable(finding: Finding) -> bool:
    return not (
        finding.category is FindingCategory.SECURITY
        and finding.severity in _UNDROPPABLE_SECURITY
    )
