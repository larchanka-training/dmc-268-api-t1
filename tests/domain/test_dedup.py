from app.domain.dedup import deduplicate
from app.domain.entities import Finding, RuleRef
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
    """У обоих new_line=None, поэтому ключ только по new_line их схлопывал."""
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


MONEY = RuleRef("Money Is Decimal", 1, "Денежные суммы - Decimal, а не float")
LOGGING = RuleRef("Logging", 1, "Диагностика - через logging")


def test_same_rule_check_on_the_same_line_in_another_category_is_collapsed() -> None:
    """У пункта правила нет своей категории: одно нарушение под двумя - дубль."""
    kept, dropped = deduplicate(
        [
            make_finding(n=1, category=FindingCategory.CORRECTNESS, rule=MONEY),
            make_finding(n=2, category=FindingCategory.READABILITY, rule=MONEY),
        ]
    )
    assert [f.id for f in kept] == [make_finding(n=1).id]
    assert [f.id for f in dropped] == [make_finding(n=2).id]


def test_another_check_of_the_same_rule_is_not_a_duplicate() -> None:
    other_check = RuleRef("Money Is Decimal", 2, "Округление - банковское")
    kept, dropped = deduplicate(
        [
            make_finding(n=1, category=FindingCategory.CORRECTNESS, rule=MONEY),
            make_finding(n=2, category=FindingCategory.READABILITY, rule=other_check),
        ]
    )
    assert len(kept) == 2
    assert dropped == []


def test_different_rules_on_the_same_line_both_kept() -> None:
    kept, _ = deduplicate(
        [
            make_finding(n=1, category=FindingCategory.CORRECTNESS, rule=MONEY),
            make_finding(n=2, category=FindingCategory.READABILITY, rule=LOGGING),
        ]
    )
    assert len(kept) == 2


def test_same_rule_check_on_another_line_is_not_a_duplicate() -> None:
    kept, _ = deduplicate(
        [
            make_finding(n=1, new_line=10, rule=MONEY),
            make_finding(n=2, new_line=11, category=FindingCategory.READABILITY, rule=MONEY),
        ]
    )
    assert len(kept) == 2


def test_same_category_is_still_a_duplicate_across_different_rules() -> None:
    """Ключ по категории остаётся: на нём держится уникальный индекс замечаний в базе."""
    kept, dropped = deduplicate([make_finding(n=1, rule=MONEY), make_finding(n=2, rule=LOGGING)])
    assert len(kept) == 1
    assert len(dropped) == 1


def test_finding_without_a_rule_does_not_collapse_a_rule_finding_of_another_category() -> None:
    kept, _ = deduplicate(
        [
            make_finding(n=1, category=FindingCategory.CORRECTNESS),
            make_finding(n=2, category=FindingCategory.READABILITY, rule=MONEY),
        ]
    )
    assert len(kept) == 2
