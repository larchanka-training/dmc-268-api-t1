"""Загрузчик золотого датасета - на мини-кейсах в `tmp_path`, без модели и сети."""

import json
from pathlib import Path
from typing import Any

import pytest

from .dataset import DatasetError, load_dataset
from .minidataset import PATH, expected_finding, response, write_case


def test_case_loads_with_its_diff_expected_findings_and_raw_responses(tmp_path: Path) -> None:
    write_case(tmp_path, "TC-01-sql", responses=[response(), "не JSON"])
    write_case(
        tmp_path,
        "ADV-01-comment",
        kind="adversarial",
        expected=[],
        injection={"forbidden_phrases": ["LGTM"], "expect_no_findings": True},
        responses=[response(), response()],
    )

    tc, adv = load_dataset(tmp_path)

    assert (tc.case_id, tc.kind, tc.mr.target_branch) == ("TC-01-sql", "bug", "develop")
    assert [f.file_path for f in tc.files] == [PATH]
    (issue,) = tc.expected_issues
    assert issue.id == "TC-01-sql/sql-injection"
    assert (issue.side, issue.line, issue.must_find) == ("new", 2, True)
    # Ответ - сырой текст: невалидный JSON - забота раннера, не загрузчика.
    assert [(r.repetition, r.text) for r in tc.responses] == [(1, response()), (2, "не JSON")]
    assert adv.case_id == "ADV-01-comment"
    assert adv.injection is not None and adv.injection.expect_no_findings


def test_removed_line_anchors_on_the_old_side(tmp_path: Path) -> None:
    removed = expected_finding(side="old", line=2, category="correctness")
    write_case(tmp_path, "TC-01-sql", expected=[removed])

    (case,) = load_dataset(tmp_path)

    assert case.expected[0].side == "old"


@pytest.mark.parametrize(
    "finding",
    [
        expected_finding(line=40),  # строки нет в hunk'е
        expected_finding(side="old", line=5),  # на старой стороне строк всего три
        expected_finding(file_path="app/other.py"),  # файла нет в диффе
    ],
    ids=["line-outside-hunk", "wrong-side", "file-not-in-diff"],
)
def test_anchor_outside_the_diff_names_the_case_and_the_finding(
    tmp_path: Path, finding: dict[str, Any]
) -> None:
    write_case(tmp_path, "TC-01-sql", expected=[finding])

    with pytest.raises(DatasetError, match=r"TC-01-sql: находка sql-injection: строки"):
        load_dataset(tmp_path)


@pytest.mark.parametrize(
    ("overrides", "finding", "message"),
    [
        ({"extra": 1}, None, r"неизвестные ключи \['extra'\]"),
        ({"case_id": "TC-02-other"}, None, "не совпадает с именем каталога"),
        ({"kind": "smoke"}, None, "kind: 'smoke'"),
        ({"max_findings": 0}, None, "max_findings 0 меньше числа must_find"),
        ({"forbidden_categories": ["security"]}, None, "запрещённой на кейсе категории"),
        ({"forbidden_categories": ["style"]}, None, "forbidden_categories: 'style'"),
        ({}, expected_finding(category="style"), "находка sql-injection, category"),
        ({}, expected_finding(severity="blocker"), "находка sql-injection, severity"),
        ({}, expected_finding(side="both"), "находка sql-injection, side"),
        ({}, expected_finding(line=True), "находка sql-injection: line"),
        ({}, expected_finding(must_find="yes"), "находка sql-injection: must_find"),
        ({}, expected_finding(new_line=2), r"находка sql-injection: неизвестные ключи"),
    ],
)
def test_expected_json_out_of_contract_is_rejected_with_the_case_name(
    tmp_path: Path, overrides: dict[str, Any], finding: dict[str, Any] | None, message: str
) -> None:
    write_case(
        tmp_path,
        "TC-01-sql",
        expected=None if finding is None else [finding],
        overrides=overrides,
    )

    with pytest.raises(DatasetError, match=rf"^TC-01-sql: .*{message}"):
        load_dataset(tmp_path)


def test_missing_key_is_rejected_rather_than_defaulted(tmp_path: Path) -> None:
    case = write_case(tmp_path, "TC-01-sql")
    data = json.loads((case / "expected.json").read_text())
    del data["forbidden_categories"]
    (case / "expected.json").write_text(json.dumps(data))

    with pytest.raises(DatasetError, match=r"нет ключей \['forbidden_categories'\]"):
        load_dataset(tmp_path)


def test_more_than_sixteen_expected_findings_is_rejected(tmp_path: Path) -> None:
    many = [expected_finding(id=f"f{n}", must_find=False) for n in range(17)]
    write_case(tmp_path, "TC-01-sql", expected=many)

    with pytest.raises(DatasetError, match="TC-01-sql: 17 эталонных находок"):
        load_dataset(tmp_path)


def test_negative_case_has_no_expected_findings(tmp_path: Path) -> None:
    write_case(tmp_path, "TC-09-negative", kind="negative")

    with pytest.raises(DatasetError, match="у негативного кейса эталонных находок нет"):
        load_dataset(tmp_path)


def test_adversarial_case_needs_injection_and_the_adversarial_kind(tmp_path: Path) -> None:
    write_case(tmp_path, "ADV-01-comment", kind="adversarial", expected=[])
    with pytest.raises(DatasetError, match="ADV-01-comment: у враждебного кейса нужен injection"):
        load_dataset(tmp_path)

    write_case(tmp_path / "other", "ADV-02-bug", kind="bug")
    with pytest.raises(DatasetError, match="ADV-02-bug: у кейса в adversarial/ kind"):
        load_dataset(tmp_path / "other")


def test_case_without_responses_or_with_a_stray_file_is_rejected(tmp_path: Path) -> None:
    case = write_case(tmp_path, "TC-01-sql")
    (case / "responses" / "run-1.json").unlink()
    with pytest.raises(DatasetError, match="нет ни одного ответа"):
        load_dataset(tmp_path)

    (case / "responses" / "best.json").write_text("{}")
    with pytest.raises(DatasetError, match=r"responses/best.json: ожидается run-<n>.json"):
        load_dataset(tmp_path)


@pytest.mark.parametrize(
    ("place", "name"),
    [
        ("adversarial", "TC-25-moved"),  # кейс-баг унесли в adversarial/
        ("adversarial", "adv-11-lower"),  # регистр не тот
        (".", "tc-02-lower"),
        (".", "TC-3-short"),  # номер не из двух цифр
        (".", "drafts"),
    ],
)
def test_directory_outside_the_case_mask_is_rejected_not_skipped(
    tmp_path: Path, place: str, name: str
) -> None:
    write_case(tmp_path, "TC-01-sql")
    (tmp_path / place / name).mkdir(parents=True)

    with pytest.raises(DatasetError, match=f"каталоги не под маску .*'{name}'"):
        load_dataset(tmp_path)


def test_empty_dataset_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(DatasetError, match="нет ни одного кейса"):
        load_dataset(tmp_path)


def test_cases_with_different_repetition_sets_are_rejected(tmp_path: Path) -> None:
    # Разброс F1 сравнивает повторы целиком: повтор 2 не у всех - другой набор кейсов.
    write_case(tmp_path, "TC-01-sql", responses=[response(), response()])
    write_case(tmp_path, "TC-02-sql")

    with pytest.raises(DatasetError, match=r"один набор повторов.*TC-02-sql - \[1\]"):
        load_dataset(tmp_path)


def _external(root: Path, case_id: str, *texts: str) -> None:
    """Ответы кейса во внешнем каталоге: `<root>/<case_id>/run-<n>.json`."""
    folder = root / case_id
    folder.mkdir(parents=True)
    for n, text in enumerate(texts, start=1):
        (folder / f"run-{n}.json").write_text(text, encoding="utf-8")


def test_responses_from_an_external_directory_replace_the_fixtures(tmp_path: Path) -> None:
    write_case(tmp_path / "ds", "TC-01-sql")
    write_case(
        tmp_path / "ds",
        "ADV-01-comment",
        kind="adversarial",
        expected=[],
        injection={"forbidden_phrases": ["LGTM"], "expect_no_findings": True},
    )
    _external(tmp_path / "live", "TC-01-sql", "живой 1", "живой 2")
    _external(tmp_path / "live", "ADV-01-comment", response(), "не JSON")

    tc, adv = load_dataset(tmp_path / "ds", responses=tmp_path / "live")

    assert [(r.repetition, r.text) for r in tc.responses] == [(1, "живой 1"), (2, "живой 2")]
    assert [(r.repetition, r.text) for r in adv.responses] == [(1, response()), (2, "не JSON")]


def test_case_without_its_directory_in_the_external_responses_is_rejected(
    tmp_path: Path,
) -> None:
    write_case(tmp_path / "ds", "TC-01-sql")
    write_case(tmp_path / "ds", "TC-02-sql")
    _external(tmp_path / "live", "TC-01-sql", response())

    with pytest.raises(DatasetError, match=r"TC-02-sql: нет каталога ответов .*live/TC-02-sql"):
        load_dataset(tmp_path / "ds", responses=tmp_path / "live")


def test_external_responses_out_of_contract_are_rejected(tmp_path: Path) -> None:
    write_case(tmp_path / "ds", "TC-01-sql")
    (tmp_path / "live" / "TC-01-sql").mkdir(parents=True)
    with pytest.raises(DatasetError, match="TC-01-sql: в .* нет ни одного ответа"):
        load_dataset(tmp_path / "ds", responses=tmp_path / "live")

    (tmp_path / "live" / "TC-01-sql" / "best.json").write_text("{}")
    with pytest.raises(DatasetError, match=r"TC-01-sql: .*best.json: ожидается run-<n>.json"):
        load_dataset(tmp_path / "ds", responses=tmp_path / "live")


def test_external_responses_with_different_repetition_sets_are_rejected(
    tmp_path: Path,
) -> None:
    write_case(tmp_path / "ds", "TC-01-sql")
    write_case(tmp_path / "ds", "TC-02-sql")
    _external(tmp_path / "live", "TC-01-sql", response(), response())
    _external(tmp_path / "live", "TC-02-sql", response())

    with pytest.raises(DatasetError, match=r"один набор повторов.*TC-02-sql - \[1\]"):
        load_dataset(tmp_path / "ds", responses=tmp_path / "live")
