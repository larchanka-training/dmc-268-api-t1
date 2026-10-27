"""Песочница воркера: входы, модели, повторная доставка, сбои GitHub и CLI.

Без инфраструктуры и сети: модель - оракул, сценарий или заглушка, GitHub -
`FakeGitHub`. Идут под обычным `uv run pytest`.
"""

import json
import shutil
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

import pytest

from ..eval.rules.corpus import CORPUS_DIR, load_case
from ..eval.rules.oracle import OracleLlmGateway
from ..fakes.github_api import FakeGitHub
from ..fakes.llm_gateway import load_llm_script
from .__main__ import main
from .inputs import from_case, from_git, load_case_file, load_raw_message
from .worker import CaseResult, SandboxConfig, run_sandbox

REPO_ROOT = Path(__file__).parents[2]
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "outline"
EXAMPLES = Path(__file__).parent / "examples"
SCRIPT = EXAMPLES / "llm-script.json"
CASE_FILE = EXAMPLES / "case.yaml"
BAD_MESSAGE = EXAMPLES / "bad-message.json"
SQL_CASE = CORPUS_DIR / "04-users-sql-injection.yaml"


def _oracle_run(
    *,
    redeliver: bool = False,
    prepare_github: Callable[[FakeGitHub, Sequence[int]], None] | None = None,
) -> CaseResult:
    case = load_case(SQL_CASE)
    (result,) = run_sandbox(
        [from_case(case)],
        SandboxConfig(llm="oracle"),
        llm=OracleLlmGateway([case]),
        redeliver=redeliver,
        prepare_github=prepare_github,
    ).cases
    return result


# --- модели -----------------------------------------------------------------


def test_the_oracle_publishes_exactly_the_marked_issues() -> None:
    result = _oracle_run()

    assert (result.status, result.acked, result.nacked) == ("completed", True, False)
    published = {(c.path, c.line) for review in result.reviews for c in review.comments}
    assert published == {
        ("app/repositories/users.py", 7),
        ("app/repositories/users.py", 12),
    }
    assert published == {(issue.path, issue.line) for issue in result.expected}


def test_the_example_script_keeps_f1_and_drops_f2() -> None:
    (result,) = run_sandbox(
        [load_case_file(CASE_FILE)],
        SandboxConfig(llm="script"),
        llm=load_llm_script(SCRIPT),
    ).cases

    assert result.status == "completed"
    comments = [c for review in result.reviews for c in review.comments]
    assert [(c.path, c.line) for c in comments] == [("app/billing/discount.py", 3)]
    assert [(issue.path, issue.line) for issue in result.expected] == [
        ("app/billing/discount.py", 7)
    ]


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


# --- доставка и сбои GitHub ---------------------------------------------------


def test_a_redelivered_message_does_not_publish_a_second_review() -> None:
    result = _oracle_run(redeliver=True)

    assert result.status == "completed"
    assert (result.deliveries, result.delivery_outcomes) == (2, ("ack", "ack"))
    assert not result.nacked
    assert len(result.reviews) == 1


def test_a_lost_review_response_does_not_duplicate_the_review() -> None:
    githubs: list[FakeGitHub] = []

    def prepare(github: FakeGitHub, numbers: Sequence[int]) -> None:
        githubs.append(github)
        github.apply_fault("review-502-stored", numbers)

    result = _oracle_run(prepare_github=prepare)

    (github,) = githubs
    # Отказ израсходован: POST …/reviews ответил 502, и воркер повторил запрос.
    assert github.create_review_failures == []
    assert (result.status, result.acked, result.nacked) == ("completed", True, False)
    assert len(result.reviews) == 1


def test_a_message_without_job_id_is_nacked() -> None:
    sandbox = run_sandbox(
        [],
        SandboxConfig(llm="stub"),
        llm=load_llm_script(SCRIPT),
        raw_messages=[load_raw_message(BAD_MESSAGE)],
        raw_labels=["bad"],
    )

    (raw,) = sandbox.raw
    assert (raw.path_or_label, raw.acked, raw.nacked) == ("bad", False, True)
    assert raw.error is not None and "job_id" in raw.error


# --- PR из git ----------------------------------------------------------------


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args],
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
    shutil.copy(FIXTURES / "deploy_report_53b6a28.py", repo / "scripts" / "deploy_report.py")
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
    assert [c["line"] for c in case["reviews"][0]["comments"]] == [3]


def test_cli_ignores_rejected_raw_messages_in_the_exit_code(tmp_path: Path) -> None:
    out = tmp_path / "out.json"

    code = main(["--llm", "stub", "--message", str(BAD_MESSAGE), "--json", str(out)])

    assert code == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["cases"] == []
    assert [(r["path_or_label"], r["nacked"]) for r in data["raw"]] == [
        (str(BAD_MESSAGE), True)
    ]


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
        (["--llm", "stub", "--github-fault", "nope"], "неизвестный сбой GitHub"),
        (["--llm", "stub", "--github-fault", "latency=-1"], "latency=-1"),
        (["--llm", "script"], "--llm-script"),
        (["--llm", "stub", "--llm-script", "x.json"], "--llm-script"),
        (["--llm", "stub", "--case-file", "missing.yaml"], "missing.yaml"),
    ],
)
def test_cli_rejects_bad_input_with_exit_code_2(
    argv: list[str], message: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(argv) == 2
    assert message in capsys.readouterr().err
