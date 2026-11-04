"""Сводка ревью: какие файлы проверены и какие риски нашлись (#279)."""

from datetime import UTC, datetime
from uuid import uuid4

from app.domain.entities import DiffAnchor, Finding, Hunk, ParsedFile
from app.domain.enums import DiffSide, FindingCategory, FindingSeverity
from app.domain.review_comment import format_summary_comment
from app.domain.review_coverage import ReviewCoverage, review_coverage

NOW = datetime(2026, 11, 4, tzinfo=UTC)


def hunk(path: str, start: int) -> Hunk:
    return Hunk(file_path=path, new_start=start, new_count=1)


def finding(category: FindingCategory, severity: FindingSeverity) -> Finding:
    return Finding(
        id=uuid4(),
        review_run_id=uuid4(),
        anchor=DiffAnchor(file_path="app/a.py", side=DiffSide.NEW, new_line=1),
        category=category,
        severity=severity,
        message="m",
        created_at=NOW,
        updated_at=NOW,
    )


def test_coverage_splits_files_by_what_the_model_saw() -> None:
    whole, half = (hunk("app/a.py", 1),), (hunk("app/b.py", 1), hunk("app/b.py", 50))
    files = (
        ParsedFile(file_path="app/a.py", hunks=whole),
        ParsedFile(file_path="app/b.py", hunks=half),
        ParsedFile(file_path="app/c.py", hunks=(hunk("app/c.py", 1),)),
        ParsedFile(file_path="uv.lock", hunks=(hunk("uv.lock", 1),)),
        ParsedFile(file_path="logo.png", is_binary=True),
    )

    coverage = review_coverage(files, shown=(*whole, half[0]))

    assert coverage == ReviewCoverage(
        reviewed=("app/a.py",),
        partial=("app/b.py",),
        not_reviewed=("app/c.py",),
        filtered=("uv.lock", "logo.png"),
    )


def test_summary_lists_risks_and_files_under_details() -> None:
    coverage = ReviewCoverage(reviewed=("app/a.py",), partial=("app/b.py",), not_reviewed=(), filtered=("uv.lock",))
    findings = (
        finding(FindingCategory.SECURITY, FindingSeverity.CRITICAL),
        finding(FindingCategory.SECURITY, FindingSeverity.HIGH),
        finding(FindingCategory.CORRECTNESS, FindingSeverity.HIGH),
        finding(FindingCategory.READABILITY, FindingSeverity.LOW),
    )

    body = format_summary_comment("Проблема.", (), "час", findings=findings, coverage=coverage)

    assert "**Риски**\n- Безопасность: 🔴 Критично — 1, 🟠 Существенно — 1\n- Корректность: 🟠 Существенно — 1" in body
    assert "Читаемость" not in body
    assert "<details>\n<summary>Файлы: проверено 1, частично 1, отброшено фильтром 1</summary>\n\n" in body
    assert "- `app/b.py`" in body and "- `uv.lock`" in body and body.endswith("</details>")


def test_summary_without_serious_findings_has_no_risks_block() -> None:
    body = format_summary_comment(
        "Проблема.", (), "час", findings=(finding(FindingCategory.READABILITY, FindingSeverity.MEDIUM),),
        coverage=ReviewCoverage(reviewed=("a.py",), partial=(), not_reviewed=(), filtered=()),
    )

    assert "Риски" not in body


def test_hostile_path_cannot_break_out_of_the_list() -> None:
    """Имя файла пишет автор PR: обратные кавычки и управляющие символы не ломают разметку."""
    coverage = ReviewCoverage(reviewed=("a`</details>`b.py", "x\ny.py"), partial=(), not_reviewed=(), filtered=())

    body = format_summary_comment("П.", (), "час", findings=(), coverage=coverage)

    assert "- `a<U+0060></details><U+0060>b.py`" in body
    assert "\ny.py" not in body
    assert body.count("</details>") == 2


def test_long_file_list_is_capped() -> None:
    paths = tuple(f"f{i}.py" for i in range(120))
    coverage = ReviewCoverage(reviewed=paths, partial=(), not_reviewed=(), filtered=())

    body = format_summary_comment("П.", (), "час", findings=(), coverage=coverage)

    assert "- `f49.py`" in body and "f50.py" not in body
    assert "- … и ещё 70" in body


def test_hostile_long_paths_keep_the_comment_under_the_github_limit() -> None:
    """Имена пишет автор PR: 4 группы по 50 путей из управляющих байт или обратных кавычек не превышают 65 536 символов."""
    for char in ("\x01", "`"):
        paths = tuple(char * 4000 + str(i) for i in range(60))
        coverage = ReviewCoverage(reviewed=paths, partial=paths, not_reviewed=paths, filtered=paths)

        body = format_summary_comment("П.", (), "час", findings=(), coverage=coverage)

        assert len(body) < 65_536, repr(char)


def test_file_without_hunks_is_not_listed() -> None:
    coverage = review_coverage((ParsedFile(file_path="new_name.py", old_path="old_name.py"),), shown=())
    assert coverage == ReviewCoverage((), (), (), ())
