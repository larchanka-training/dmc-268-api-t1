"""Разбор `.review/rules.md` и отбор правил под файлы диффа.

Владелец репозитория пишет правила Markdown'ом (форма — в
`prompts/review/custom-rules.md`):

    ## Error Handling Standards

    - pattern: `app/**/*.py`
    - exclude: `**/tests/**`

    1. Отказ возвращается значением Result, а не исключением

Раздел второго уровня — правило, строки `pattern:` / `exclude:` — область
действия, нумерованные пункты - проверки. Имя правила уникально в файле, а
номер пункта - его позиция среди проверок, а не номер, написанный автором:
по паре «имя, номер» замечание ссылается на пункт. Всё остальное отбрасывается: в
контекст модели попадают только распознанные поля, поэтому проза вокруг
правил — и всё, что подложено под её видом, — модель не видит. Содержимое
блоков кода (```) не разбирается вовсе: пример правила внутри него правилом
не становится.

Функции чистые: текст приходит строкой, уходят доменные записи.
"""

import re
from collections.abc import Iterable
from pathlib import PurePosixPath

from app.domain.diff_parser import split_lines
from app.domain.entities import RejectedRule, RepoRule

RULES_PATH = ".review/rules.md"

_RULE_HEADING = re.compile(r"^##(?!#)\s+(?P<name>.+?)\s*#*\s*$")
_ANY_HEADING = re.compile(r"^#{1,6}\s")
_FIELD = re.compile(
    r"^\s*(?:[-*+]\s+)?(?P<key>pattern|exclude)\s*:\s*(?P<value>.+?)\s*$",
    re.IGNORECASE,
)
_CHECK = re.compile(r"^\s*\d+[.)]\s+(?P<text>.+?)\s*$")
_FENCE = re.compile(r"^\s*(```|~~~)")


class _Draft:
    """Правило в процессе разбора — до проверки, что все части на месте."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.patterns: list[str] = []
        self.excludes: list[str] = []
        self.checks: list[str] = []

    def finish(self) -> RepoRule | RejectedRule:
        missing = [
            label
            for label, part in (
                ("нет паттерна (pattern)", self.patterns),
                ("нет проверок", self.checks),
            )
            if not part
        ]
        if missing:
            return RejectedRule(name=self.name, reason="; ".join(missing))
        return RepoRule(
            name=self.name,
            patterns=tuple(self.patterns),
            excludes=tuple(self.excludes),
            checks=tuple(self.checks),
        )


def parse_rules_markdown(text: str) -> tuple[tuple[RepoRule, ...], tuple[RejectedRule, ...]]:
    """Разобрать `rules.md` на принятые и отклонённые правила, в порядке файла."""
    drafts: list[_Draft] = []
    current: _Draft | None = None
    in_fence = False

    for line in split_lines(text):
        if _FENCE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if (heading := _RULE_HEADING.match(line)) is not None:
            current = _Draft(heading.group("name"))
            drafts.append(current)
            continue
        if _ANY_HEADING.match(line):
            # Заголовок другого уровня закрывает правило: текст под ним уже
            # не относится к разделу `##`.
            current = None
            continue
        if current is None:
            continue
        if (field := _FIELD.match(line)) is not None:
            value = _unquote(field.group("value"))
            target = current.patterns if field.group("key").lower() == "pattern" else current.excludes
            if value:
                target.append(value)
        elif (check := _CHECK.match(line)) is not None:
            current.checks.append(check.group("text"))

    # Имя - ключ ссылки замечания на правило: второе принятое правило с тем же
    # именем сделало бы ссылку неоднозначной. Отклонённый раздел имя не занимает.
    accepted: list[RepoRule] = []
    rejected: list[RejectedRule] = []
    taken: set[str] = set()
    for draft in drafts:
        result = draft.finish()
        if isinstance(result, RejectedRule):
            rejected.append(result)
        elif result.name in taken:
            rejected.append(RejectedRule(name=result.name, reason="имя правила повторяется"))
        else:
            taken.add(result.name)
            accepted.append(result)
    return tuple(accepted), tuple(rejected)


def rule_applies_to(rule: RepoRule, path: str) -> bool:
    """Файл подходит под паттерн правила и не подпадает под исключение."""
    candidate = PurePosixPath(path)
    return any(candidate.full_match(p) for p in rule.patterns) and not any(
        candidate.full_match(e) for e in rule.excludes
    )


def applicable_rules(
    rules: Iterable[RepoRule], paths: Iterable[str]
) -> tuple[tuple[RepoRule, tuple[str, ...]], ...]:
    """Правила, под которые попал хотя бы один файл, — вместе с этими файлами."""
    path_list = list(paths)
    matched: list[tuple[RepoRule, tuple[str, ...]]] = []
    for rule in rules:
        files = tuple(p for p in path_list if rule_applies_to(rule, p))
        if files:
            matched.append((rule, files))
    return tuple(matched)


def _unquote(value: str) -> str:
    """Снять обрамляющие обратные кавычки или кавычки: `app/**` → app/**."""
    for quote in ("`", '"', "'"):
        if len(value) >= 2 and value[0] == quote and value[-1] == quote:
            return value[1:-1].strip()
    return value
