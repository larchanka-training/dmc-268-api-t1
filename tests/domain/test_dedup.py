from app.domain.dedup import deduplicate
from app.domain.entities import Finding
from app.domain.enums import DiffSide, FindingCategory

from .conftest import make_finding


def test_exact_duplicate_is_collapsed() -> None:
    kept, dropped = deduplicate([make_finding(n=1), make_finding(n=2)])
    assert len(kept) == 1
    assert len(dropped) == 1
    assert kept[0].id == make_finding(n=1).id


def test_same_line_different_category_both_kept() -> None:
    kept, dropped = deduplicate(
        [
            make_finding(n=1, category=FindingCategory.SECURITY),
            make_finding(n=2, category=FindingCategory.PERFORMANCE),
        ]
    )
    assert len(kept) == 2
    assert dropped == []


def test_different_lines_both_kept() -> None:
    kept, _ = deduplicate([make_finding(n=1, new_line=10), make_finding(n=2, new_line=20)])
    assert len(kept) == 2


def test_order_is_preserved_and_first_wins() -> None:
    kept, _ = deduplicate(
        [make_finding(n=1, new_line=30), make_finding(n=2, new_line=10), make_finding(n=3, new_line=30)]
    )
    assert [f.anchor.new_line for f in kept] == [30, 10]


def test_empty_input() -> None:
    assert deduplicate([]) == ([], [])


def _old(n: int, line: int) -> Finding:
    return make_finding(n=n, side=DiffSide.OLD, old_line=line, new_line=None)


def test_old_side_findings_on_different_lines_both_kept() -> None:
    """Both carry new_line=None, so keying on new_line alone collapsed them."""
    kept, dropped = deduplicate([_old(1, 7), _old(2, 42)])
    assert [f.anchor.old_line for f in kept] == [7, 42]
    assert dropped == []


def test_old_side_repeat_of_same_line_is_collapsed() -> None:
    kept, dropped = deduplicate([_old(1, 7), _old(2, 7)])
    assert len(kept) == 1
    assert len(dropped) == 1


def test_same_line_number_on_each_side_both_kept() -> None:
    kept, _ = deduplicate(
        [make_finding(n=1, new_line=7), _old(2, 7)]
    )
    assert len(kept) == 2
