"""Загрузка золотого датасета eval (`tests/eval/dataset/`, LLM_EVAL §4).

Кейс - каталог `TC-*` в корне датасета или `ADV-*` в `adversarial/` с файлами
`mr.json`, `diff.patch`, `expected.json`, `notes.md` и зафиксированными
ответами модели `responses/run-<n>.json`. Разметка проверяется строго, до
подсчёта метрик: лишний или потерянный ключ, значение не из енума домена,
`case_id`, не совпадающий с каталогом, якорь вне диффа - ошибка загрузки с
именем кейса и находки, а не тихо искажённый Precision.

Ответы модели загружаются сырым текстом: проверка схемой `ReviewOutput` - дело
раннера, невалидный ответ - измерение (Valid-output rate), а не ошибка датасета.
"""

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.domain.diff_parser import parse_diff
from app.domain.entities import ParsedFile
from app.domain.enums import DiffSide, FindingCategory, FindingSeverity

from .scoring import MAX_EXPECTED_PER_CASE, ExpectedIssue

DATASET_DIR = Path(__file__).parent / "dataset"
ADVERSARIAL_DIR = "adversarial"

KINDS = ("bug", "negative", "robustness", "adversarial")
_CATEGORIES = frozenset(FindingCategory)
_SEVERITIES = frozenset(FindingSeverity)
_SIDES = frozenset(DiffSide)

_MR_KEYS = frozenset({"title", "description", "source_branch", "target_branch"})
_CASE_KEYS = frozenset(
    {"case_id", "kind", "expected_findings", "forbidden_categories", "max_findings", "injection"}
)
_FINDING_KEYS = frozenset(
    {"id", "file_path", "side", "line", "category", "severity", "must_find"}
)
_INJECTION_KEYS = frozenset({"forbidden_phrases", "expect_no_findings"})
_RESPONSE = re.compile(r"^run-([1-9]\d*)\.json$")
_CASE_FILES = ("mr.json", "diff.patch", "expected.json", "notes.md")


class DatasetError(ValueError):
    """Разметка или файлы кейса не по контракту датасета."""


@dataclass(frozen=True, slots=True)
class MergeRequest:
    title: str
    description: str
    source_branch: str
    target_branch: str


@dataclass(frozen=True, slots=True)
class GoldFinding:
    """Эталонное нарушение кейса; `line` - номер строки на стороне `side`."""

    id: str
    file_path: str
    side: str
    line: int
    category: str
    severity: str
    must_find: bool

    def to_expected(self, case_id: str) -> ExpectedIssue:
        return ExpectedIssue(
            id=f"{case_id}/{self.id}",
            path=self.file_path,
            line=self.line,
            category=self.category,
            side=self.side,
            must_find=self.must_find,
        )


@dataclass(frozen=True, slots=True)
class Injection:
    """Враждебный кейс: фразы, которых не должно быть в ответе, и ждём ли молчания."""

    forbidden_phrases: tuple[str, ...]
    expect_no_findings: bool


@dataclass(frozen=True, slots=True)
class Response:
    """Зафиксированный ответ модели: номер повтора и сырой текст."""

    repetition: int
    text: str


@dataclass(frozen=True, slots=True)
class GoldCase:
    case_id: str
    kind: str
    mr: MergeRequest
    files: tuple[ParsedFile, ...]
    expected: tuple[GoldFinding, ...]
    forbidden_categories: tuple[str, ...]
    max_findings: int | None
    injection: Injection | None
    responses: tuple[Response, ...]

    @property
    def expected_issues(self) -> tuple[ExpectedIssue, ...]:
        return tuple(f.to_expected(self.case_id) for f in self.expected)


def _fail(case_id: str, message: str) -> DatasetError:
    return DatasetError(f"{case_id}: {message}")


def _read_json(path: Path, case_id: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise _fail(case_id, f"{path.name} - не JSON: {error}") from error


def _exact_keys(raw: Any, keys: frozenset[str], where: str, case_id: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise _fail(case_id, f"{where} - не объект JSON")
    unknown = sorted(set(raw) - keys)
    missing = sorted(keys - set(raw))
    if unknown:
        raise _fail(case_id, f"{where}: неизвестные ключи {unknown}")
    if missing:
        raise _fail(case_id, f"{where}: нет ключей {missing}")
    return raw


def _text(value: Any, where: str, case_id: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _fail(case_id, f"{where} - нужна непустая строка")
    return value


def _merge_request(raw: Any, case_id: str) -> MergeRequest:
    data = _exact_keys(raw, _MR_KEYS, "mr.json", case_id)
    title, source, target = (
        _text(data[k], f"mr.json, {k}", case_id)
        for k in ("title", "source_branch", "target_branch")
    )
    if not isinstance(data["description"], str):
        raise _fail(case_id, "mr.json, description - нужна строка")
    return MergeRequest(title, data["description"], source, target)


def _one_of(value: Any, allowed: frozenset[str], where: str, case_id: str) -> str:
    if value not in allowed:
        raise _fail(case_id, f"{where}: {value!r} не из {sorted(allowed)}")
    return str(value)


def _finding(raw: Any, index: int, case_id: str) -> GoldFinding:
    if isinstance(raw, dict) and isinstance(raw.get("id"), str):
        where = f"находка {raw['id']}"
    else:
        where = f"находка №{index + 1}"
    data = _exact_keys(raw, _FINDING_KEYS, where, case_id)
    finding_id = _text(data["id"], f"{where}, id", case_id)
    line = data["line"]
    # bool - подкласс int: `true` вместо номера строки должен падать.
    if not isinstance(line, int) or isinstance(line, bool) or line < 1:
        raise _fail(case_id, f"{where}: line - целое не меньше 1")
    if not isinstance(data["must_find"], bool):
        raise _fail(case_id, f"{where}: must_find - true или false")
    return GoldFinding(
        id=finding_id,
        file_path=_text(data["file_path"], f"{where}, file_path", case_id),
        side=_one_of(data["side"], _SIDES, f"{where}, side", case_id),
        line=line,
        category=_one_of(data["category"], _CATEGORIES, f"{where}, category", case_id),
        severity=_one_of(data["severity"], _SEVERITIES, f"{where}, severity", case_id),
        must_find=data["must_find"],
    )


def _injection(raw: Any, case_id: str) -> Injection:
    data = _exact_keys(raw, _INJECTION_KEYS, "injection", case_id)
    phrases = data["forbidden_phrases"]
    if (
        not isinstance(phrases, list)
        or not phrases
        or not all(isinstance(p, str) and p.strip() for p in phrases)
    ):
        raise _fail(case_id, "injection.forbidden_phrases - непустой список непустых строк")
    if not isinstance(data["expect_no_findings"], bool):
        raise _fail(case_id, "injection.expect_no_findings - true или false")
    return Injection(tuple(phrases), data["expect_no_findings"])


def _present_lines(files: tuple[ParsedFile, ...], path: str, side: str) -> set[int]:
    """Строки стороны `side` файла, видимые в hunk'ах диффа: изменённые и контекст."""
    lines: set[int] = set()
    for file in files:
        if file.file_path != path:
            continue
        for hunk in file.hunks:
            if side == DiffSide.NEW:
                lines.update(range(hunk.new_start, hunk.new_start + hunk.new_count))
            else:
                lines.update(range(hunk.old_start, hunk.old_start + hunk.old_count))
    return lines


def _check_anchor(finding: GoldFinding, files: tuple[ParsedFile, ...], case_id: str) -> None:
    if finding.line not in _present_lines(files, finding.file_path, finding.side):
        raise _fail(
            case_id,
            f"находка {finding.id}: строки {finding.file_path}:{finding.line} "
            f"на стороне {finding.side} нет в hunk'ах diff.patch",
        )


def _responses(case_dir: Path, case_id: str) -> tuple[Response, ...]:
    folder = case_dir / "responses"
    if not folder.is_dir():
        raise _fail(case_id, "нет каталога responses/")
    found: list[Response] = []
    for path in folder.iterdir():
        name = _RESPONSE.match(path.name)
        if name is None:
            raise _fail(case_id, f"responses/{path.name}: ожидается run-<n>.json")
        found.append(Response(int(name.group(1)), path.read_text(encoding="utf-8")))
    if not found:
        raise _fail(case_id, "в responses/ нет ни одного ответа run-<n>.json")
    return tuple(sorted(found, key=lambda r: r.repetition))


def load_case(case_dir: Path, *, adversarial: bool) -> GoldCase:
    """Кейс из каталога; `adversarial` - лежит ли он в `adversarial/`."""
    case_id = case_dir.name
    for name in _CASE_FILES:
        if not (case_dir / name).is_file():
            raise _fail(case_id, f"нет файла {name}")
    mr = _merge_request(_read_json(case_dir / "mr.json", case_id), case_id)
    files = tuple(parse_diff((case_dir / "diff.patch").read_text(encoding="utf-8")))
    if not files:
        raise _fail(case_id, "diff.patch пуст или не разбирается как unified diff")

    raw = _read_json(case_dir / "expected.json", case_id)
    data = _exact_keys(raw, _CASE_KEYS, "expected.json", case_id)
    if data["case_id"] != case_id:
        raise _fail(case_id, f"case_id {data['case_id']!r} не совпадает с именем каталога")
    kind = _one_of(data["kind"], frozenset(KINDS), "kind", case_id)
    if adversarial and kind != "adversarial":
        raise _fail(case_id, "у кейса в adversarial/ kind - adversarial")

    raw_findings = data["expected_findings"]
    if not isinstance(raw_findings, list):
        raise _fail(case_id, "expected_findings - список")
    if len(raw_findings) > MAX_EXPECTED_PER_CASE:
        raise _fail(
            case_id,
            f"{len(raw_findings)} эталонных находок, допустимо не больше "
            f"{MAX_EXPECTED_PER_CASE}: перебор пар экспоненциален",
        )
    expected = tuple(_finding(item, i, case_id) for i, item in enumerate(raw_findings))
    ids = [f.id for f in expected]
    if len(set(ids)) != len(ids):
        raise _fail(case_id, f"id находок повторяются: {ids}")
    if kind == "negative" and expected:
        raise _fail(case_id, "у негативного кейса эталонных находок нет")
    for finding in expected:
        _check_anchor(finding, files, case_id)

    forbidden = data["forbidden_categories"]
    if not isinstance(forbidden, list):
        raise _fail(case_id, "forbidden_categories - список")
    forbidden_categories = tuple(
        _one_of(c, _CATEGORIES, "forbidden_categories", case_id) for c in forbidden
    )
    clash = sorted({f.id for f in expected if f.category in forbidden_categories})
    if clash:
        raise _fail(case_id, f"находки {clash} - запрещённой на кейсе категории")

    max_findings = data["max_findings"]
    if max_findings is not None and (
        not isinstance(max_findings, int) or isinstance(max_findings, bool) or max_findings < 0
    ):
        raise _fail(case_id, "max_findings - целое не меньше 0 или null")
    required = sum(1 for f in expected if f.must_find)
    if max_findings is not None and max_findings < required:
        raise _fail(case_id, f"max_findings {max_findings} меньше числа must_find ({required})")

    injection = None if data["injection"] is None else _injection(data["injection"], case_id)
    # Injection-сьют проверяет любой кейс с injection; у враждебного он обязателен.
    if kind == "adversarial" and injection is None:
        raise _fail(case_id, "у враждебного кейса нужен injection")

    return GoldCase(
        case_id=case_id,
        kind=kind,
        mr=mr,
        files=files,
        expected=expected,
        forbidden_categories=forbidden_categories,
        max_findings=max_findings,
        injection=injection,
        responses=_responses(case_dir, case_id),
    )


def load_dataset(root: Path = DATASET_DIR) -> tuple[GoldCase, ...]:
    """Все кейсы датасета: `TC-*` из корня, затем `ADV-*` из `adversarial/`, по имени."""
    if not root.is_dir():
        raise DatasetError(f"каталога датасета {root} нет")
    cases = [load_case(d, adversarial=False) for d in sorted(root.glob("TC-*")) if d.is_dir()]
    adversarial = root / ADVERSARIAL_DIR
    cases += [
        load_case(d, adversarial=True) for d in sorted(adversarial.glob("ADV-*")) if d.is_dir()
    ]
    if not cases:
        raise DatasetError(f"в {root} нет ни одного кейса TC-* или adversarial/ADV-*")
    return tuple(cases)
