from app.domain.diff import validate_anchor
from app.domain.entities import DiffAnchor
from app.domain.enums import DiffSide


def test_anchor_on_a_changed_line_is_accepted(hunks) -> None:
    anchor = DiffAnchor("app/main.py", DiffSide.NEW, new_line=11)
    assert validate_anchor(anchor, hunks).ok


def test_line_outside_the_change_is_rejected(hunks) -> None:
    anchor = DiffAnchor("app/main.py", DiffSide.NEW, new_line=400)
    verdict = validate_anchor(anchor, hunks)
    assert not verdict.ok
    assert "outside the changed lines" in (verdict.error or "")


def test_file_absent_from_the_diff_is_rejected(hunks) -> None:
    anchor = DiffAnchor("app/untouched.py", DiffSide.NEW, new_line=11)
    verdict = validate_anchor(anchor, hunks)
    assert not verdict.ok
    assert "not in the diff" in (verdict.error or "")


def test_old_side_uses_the_old_line_set(hunks) -> None:
    assert validate_anchor(DiffAnchor("app/main.py", DiffSide.OLD, old_line=7), hunks).ok
    assert not validate_anchor(
        DiffAnchor("app/main.py", DiffSide.OLD, old_line=10), hunks
    ).ok


def test_anchor_without_a_line_number_is_rejected(hunks) -> None:
    verdict = validate_anchor(DiffAnchor("app/main.py", DiffSide.NEW), hunks)
    assert not verdict.ok
    assert "no line number" in (verdict.error or "")
