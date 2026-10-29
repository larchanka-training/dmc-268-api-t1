"""Песочница воркера: входы, модели и CLI.

Без инфраструктуры и сети: модель - оракул, сценарий или заглушка, GitHub -
`FakeGitHub`. Идут под обычным `uv run pytest`.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from ..eval.rules.corpus import CORPUS_DIR, load_case
from ..eval.rules.oracle import OracleLlmGateway
from ..fakes.llm_gateway import load_llm_script
from .__main__ import main
from .inputs import from_case, from_git, load_case_file
from .worker import SandboxConfig, run_sandbox

REPO_ROOT = Path(__file__).parents[2]
EXAMPLES = Path(__file__).parent / "examples"
SCRIPT = EXAMPLES / "llm-script.json"
CASE_FILE = EXAMPLES / "case.yaml"
SQL_CASE = CORPUS_DIR / "04-users-sql-injection.yaml"


# --- модели -----------------------------------------------------------------


def test_the_oracle_publishes_exactly_the_marked_issues() -> None:
    case = load_case(SQL_CASE)
    (result,) = run_sandbox(
        [from_case(case)], SandboxConfig(llm="oracle"), llm=OracleLlmGateway([case])
    ).cases

    assert (result.status, result.acked, result.nacked) == ("completed", True, False)
    comments = {
        (c.path, c.line): c.body for review in result.reviews for c in review.comments
    }
    assert set(comments) == {
        ("app/repositories/users.py", 7),
        ("app/repositories/users.py", 12),
    }
    assert set(comments) == {(issue.path, issue.line) for issue in result.expected}
    # Пункт правила виден оракулу, только если `.review/rules.md` попал в базу PR.
    assert {f.line: f.rule for f in result.findings} == {7: None, 12: "Logging#1"}
    assert "правилу «Logging» (пункт 1" in comments[("app/repositories/users.py", 12)]


def test_the_example_script_drops_f1_and_keeps_f2() -> None:
    (result,) = run_sandbox(
        [load_case_file(CASE_FILE)],
        SandboxConfig(llm="script"),
        llm=load_llm_script(SCRIPT),
    ).cases

    assert result.status == "completed"
    comments = [c for review in result.reviews for c in review.comments]
    published = [(c.path, c.line) for c in comments]
    assert published == [("app/billing/discount.py", 7)]
    assert published == [(issue.path, issue.line) for issue in result.expected]


def test_a_model_error_on_review_fails_the_run(tmp_path: Path) -> None:
    script = tmp_path / "script.json"
    script.write_text(json.dumps([{"error": "output"}]), encoding="utf-8")

    (result,) = run_sandbox(
        [load_case_file(CASE_FILE)],
        SandboxConfig(llm="script"),
        llm=load_llm_script(script),
    ).cases

    assert result.status == "failed"
    assert result.failure_reason
    assert result.reviews == ()


# --- PR из git ----------------------------------------------------------------


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        [
            "git", "-C", str(repo),
            "-c", "user.name=t", "-c", "user.email=t@t",
            "-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false",
            *args,
        ],
        check=True,
        capture_output=True,
    )


def test_from_git_takes_both_sides_of_the_range(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-q")
    (tmp_path / "keep.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "gone.py").write_text("y = 2\n", encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "база")
    (tmp_path / "keep.py").write_text("x = 3\n", encoding="utf-8")
    (tmp_path / "new.py").write_text("z = 4\n", encoding="utf-8")
    (tmp_path / "gone.py").unlink()
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "feat: правка\n\nОписание PR.")

    pr = from_git("HEAD~1..HEAD", tmp_path)

    assert pr.base_files == {"keep.py": "x = 1\n", "gone.py": "y = 2\n"}
    assert pr.head_files == {"keep.py": "x = 3\n", "new.py": "z = 4\n", "gone.py": None}
    assert (pr.pr_title, pr.pr_description) == ("feat: правка", "Описание PR.")
    assert pr.expected == ()


def test_pr_with_a_python_file_over_256_lines_completes(tmp_path: Path) -> None:
    """PR с Python-файлом длиннее 256 строк проходит до конца (#226).

    Прогон в дочернем процессе, как `test_large_file_does_not_crash_interpreter`:
    segfault tree-sitter в процессе pytest убил бы весь прогон, а не один тест.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "commit", "-q", "--allow-empty", "-m", "база")
    (repo / "scripts").mkdir()
    # Тот же генератор, что в #227: 300 функций - больше 256 строк, на 0.26.0 процесс падал.
    source = "".join(f"def f{i}():\n    return {i}\n\n" for i in range(300))
    (repo / "scripts" / "deploy_report.py").write_text(source, encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "feat: отчёт деплоя")
    out = tmp_path / "out.json"

    result = subprocess.run(
        [
            sys.executable, "-X", "faulthandler", "-m", "tests.sandbox",
            "--llm", "stub", "--from-git", "HEAD~1..HEAD",
            "--repo", str(repo), "--json", str(out),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )  # fmt: skip

    assert result.returncode == 0, result.stderr
    (case,) = json.loads(out.read_text(encoding="utf-8"))["cases"]
    assert (case["status"], case["acked"]) == ("completed", True)


# --- CLI ----------------------------------------------------------------------


def test_cli_runs_the_script_and_writes_json(tmp_path: Path) -> None:
    out = tmp_path / "out.json"

    code = main(
        [
            "--llm", "script", "--llm-script", str(SCRIPT),
            "--case-file", str(CASE_FILE), "--json", str(out),
        ]
    )  # fmt: skip

    assert code == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    (case,) = data["cases"]
    assert case["status"] == "completed"
    assert [c["line"] for c in case["reviews"][0]["comments"]] == [7]


def test_cli_exit_code_is_1_when_a_pr_run_fails(tmp_path: Path) -> None:
    script = tmp_path / "script.json"
    script.write_text(json.dumps([{"error": "output"}]), encoding="utf-8")

    code = main(
        ["--llm", "script", "--llm-script", str(script), "--case-file", str(CASE_FILE)]
    )

    assert code == 1


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (["--llm", "script"], "--llm-script"),
        (["--llm", "stub", "--llm-script", "x.json"], "--llm-script"),
        (["--llm", "stub", "--case-file", "missing.yaml"], "missing.yaml"),
        (["--llm", "oracle", "--case", "03-clean-refund", "--case", "03-clean-refund"],
         "app/domain/refunds.py"),
    ],
)
def test_cli_rejects_bad_input_with_exit_code_2(
    argv: list[str], message: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(argv) == 2
    assert message in capsys.readouterr().err


def test_cli_rejects_a_non_positive_max_output_tokens() -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["--llm", "stub", "--max-output-tokens", "0"])

    assert exit_info.value.code == 2


def test_cli_passes_max_output_tokens_to_the_worker(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Потолок ответа доходит до проверки окна воркера: больше окна - отказ."""
    code = main(
        ["--llm", "stub", "--case", "03-clean-refund", "--window", "8192",
         "--max-output-tokens", "100000"]
    )  # fmt: skip

    assert code == 2
    assert "--max-output-tokens" in capsys.readouterr().err
