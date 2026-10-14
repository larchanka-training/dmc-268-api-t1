from app.domain.diff import validate_anchor
from app.domain.diff_parser import parse_diff
from app.domain.entities import DiffAnchor
from app.domain.enums import DiffSide

SAMPLE = (
    "diff --git a/app/x.py b/app/x.py\n"
    "--- a/app/x.py\n+++ b/app/x.py\n"
    "@@ -8,3 +8,4 @@\n"
    " context\n"
    "-old line\n"
    "+new line\n"
    "+another\n"
)


def test_parse_diff_collects_changed_new_lines() -> None:
    hunks = parse_diff(SAMPLE)
    assert len(hunks) == 1
    assert hunks[0].file_path == "app/x.py"
    assert 10 in hunks[0].changed_new_lines


def test_anchor_on_unchanged_line_fails_validation() -> None:
    hunks = parse_diff(SAMPLE)
    anchor = DiffAnchor(file_path="app/x.py", side=DiffSide.NEW, new_line=8)
    assert not validate_anchor(anchor, hunks).ok
