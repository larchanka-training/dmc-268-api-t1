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

Функции чистые: текст приходит строкой, уходят доменные записи. Разбор один -
`outline_rules_markdown`, с номерами строк для редактора песочницы правил;
ревью берёт из него правила без позиций (`parse_rules_markdown`).
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass

from app.domain.diff_parser import split_lines
from app.domain.entities import RejectedRule, RepoRule, RepoRules
from app.domain.enums import RulesStatus
from app.domain.glob import glob_full_match

RULES_PATH = ".review/rules.md"

# Закрывающие `#` — часть заголовка только после пробела (CommonMark): `## C#` — правило `C#`.
_RULE_HEADING = re.compile(r"^##(?!#)\s+(?P<name>.+?)(?:\s+#+)?\s*$")
_ANY_HEADING = re.compile(r"^#{1,6}\s")
_FIELD = re.compile(
    r"^\s*(?:[-*+]\s+)?(?P<key>pattern|exclude)\s*:\s*(?P<value>.+?)\s*$",
    re.IGNORECASE,
)
_CHECK = re.compile(r"^\s*\d+[.)]\s+(?P<text>.+?)\s*$")
# Ограда кода по CommonMark: в info-строке обратной ограды нет обратных кавычек;
# закрывает тот же символ, не короче открывающей, и после неё только пробелы.
# Отступ любой, а не до трёх пробелов: списки парсер не разбирает, а пример кода
# в пункте `10.` сдвинут на четыре.
_FENCE_OPEN = re.compile(r"^\s*(?P<fence>`{3,}|~{3,})(?P<info>.*)$")
_FENCE_CLOSE = re.compile(r"^\s*(?P<fence>`{3,}|~{3,})[ \t]*$")
UNCLOSED_FENCE_REASON = "незакрытый блок кода"


def _opening_fence(line: str) -> str | None:
    """Ограда, которой line открывает блок кода, или None, если не открывает."""
    match = _FENCE_OPEN.match(line)
    if match is None:
        return None
    fence = match.group("fence")
    if fence[0] == "`" and "`" in match.group("info"):
        return None
    return fence


def _closes_fence(line: str, fence: str) -> bool:
    match = _FENCE_CLOSE.match(line)
    if match is None:
        return False
    closing = match.group("fence")
    return closing[0] == fence[0] and len(closing) >= len(fence)


@dataclass(frozen=True, slots=True)
class RuleOutline:
    """Принятое правило и где оно в тексте: строка заголовка и строки пунктов, с 1."""

    rule: RepoRule
    line: int
    check_lines: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class RejectedOutline:
    """Раздел, не ставший правилом, и строка его заголовка, с 1."""

    rejected: RejectedRule
    line: int


class _Draft:
    """Правило в процессе разбора — до проверки, что все части на месте."""

    def __init__(self, name: str, line: int) -> None:
        self.name = name
        self.line = line
        self.patterns: list[str] = []
        self.excludes: list[str] = []
        self.checks: list[str] = []
        self.check_lines: list[int] = []

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
    accepted, rejected = outline_rules_markdown(text)
    return tuple(o.rule for o in accepted), tuple(o.rejected for o in rejected)


def rules_from_text(text: str) -> RepoRules:
    """Правила из прочитанного текста `rules.md`: без принятых - `empty`, иначе `loaded`.

    Одно правило для ревью и песочницы; нет файла, он больше лимита или не
    прочитался - статус решает вызывающий, текста у него нет.
    """
    rules, rejected = parse_rules_markdown(text)
    if not rules:
        return RepoRules(status=RulesStatus.EMPTY, rejected=rejected)
    return RepoRules(status=RulesStatus.LOADED, rules=rules, rejected=rejected)


def outline_rules_markdown(text: str) -> tuple[tuple[RuleOutline, ...], tuple[RejectedOutline, ...]]:
    """Разобрать `rules.md` с номерами строк, с 1; строки делятся только по `\\n`, как в git."""
    drafts: list[_Draft] = []
    current: _Draft | None = None
    fence: str | None = None
    swallowed: list[tuple[int, str]] = []

    for number, line in enumerate(split_lines(text), start=1):
        if fence is not None:
            if _closes_fence(line, fence):
                fence = None
                swallowed = []
            else:
                swallowed.append((number, line))
            continue
        if (opening := _opening_fence(line)) is not None:
            fence = opening
            continue
        if (heading := _RULE_HEADING.match(line)) is not None:
            current = _Draft(heading.group("name"), number)
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
            current.check_lines.append(number)

    # Имя - ключ ссылки замечания на правило: второе принятое правило с тем же
    # именем сделало бы ссылку неоднозначной. Отклонённый раздел имя не занимает.
    accepted: list[RuleOutline] = []
    rejected: list[RejectedOutline] = []
    taken: set[str] = set()
    for draft in drafts:
        result = draft.finish()
        if isinstance(result, RejectedRule):
            rejected.append(RejectedOutline(result, draft.line))
        elif result.name in taken:
            rejected.append(RejectedOutline(RejectedRule(name=result.name, reason="имя правила повторяется"), draft.line))
        else:
            taken.add(result.name)
            accepted.append(RuleOutline(result, draft.line, tuple(draft.check_lines)))
    if fence is not None:
        # Незакрытый блок молча съел бы остаток файла: правила в нём не видны
        # ни как принятые, ни как отклонённые, и автор не узнает почему.
        rejected.extend(
            RejectedOutline(RejectedRule(name=heading.group("name"), reason=UNCLOSED_FENCE_REASON), number)
            for number, line in swallowed
            if (heading := _RULE_HEADING.match(line)) is not None
        )
    return tuple(accepted), tuple(rejected)


def rule_applies_to(rule: RepoRule, path: str) -> bool:
    """Файл подходит под паттерн правила и не подпадает под исключение."""
    return any(_glob_matches(path, p) for p in rule.patterns) and not any(
        _glob_matches(path, e) for e in rule.excludes
    )


def _glob_matches(path: str, pattern: str) -> bool:
    """Glob с семантикой `.gitignore`.

    Шаблон без `/` (кроме завершающего) — имя на любой глубине: `*.py` = `**/*.py`.
    Завершающий `/` — только каталог со всем содержимым: `app/` = `**/app/**`. Шаблон без
    завершающего `/` совпадает и с файлом, и с содержимым каталога: `app/api` = `app/api/**`.
    `/` в начале или середине привязывает шаблон к корню репозитория.
    Сопоставление - `glob_full_match`, без регулярных выражений: шаблон не может
    повесить воркер (#222).
    """
    body = pattern.strip("/")
    if not body:
        return False
    anchored = pattern.startswith("/") or "/" in body
    if not anchored:
        body = "**/" + body
    if pattern.endswith("/"):
        return glob_full_match(path, body + "/**")
    return glob_full_match(path, body) or glob_full_match(path, body + "/**")


def matching_files(rule: RepoRule, paths: Iterable[str]) -> tuple[str, ...]:
    """Файлы из `paths`, к которым правило применимо, в их порядке."""
    return tuple(p for p in paths if rule_applies_to(rule, p))


def applicable_rules(
    rules: Iterable[RepoRule], paths: Iterable[str]
) -> tuple[tuple[RepoRule, tuple[str, ...]], ...]:
    """Правила, под которые попал хотя бы один файл, — вместе с этими файлами."""
    path_list = list(paths)
    matched: list[tuple[RepoRule, tuple[str, ...]]] = []
    for rule in rules:
        if files := matching_files(rule, path_list):
            matched.append((rule, files))
    return tuple(matched)


def _unquote(value: str) -> str:
    """Снять обрамляющие обратные кавычки или кавычки: `app/**` → app/**."""
    for quote in ("`", '"', "'"):
        if len(value) >= 2 and value[0] == quote and value[-1] == quote:
            return value[1:-1].strip()
    return value
