"""Плагин `tests/strict_skips.py`: код возврата pytest при пропуске теста.

Каждый случай — отдельный pytest в подпроцессе над временным файлом: так
пропуск внутри не задевает внешний прогон, который сам идёт с
`PYTEST_FAIL_ON_SKIP=1` в CI.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

SKIPPED = "import pytest\n\ndef test_ok(): pass\n\ndef test_skipped(): pytest.skip('нет базы')\n"
PASSED = "def test_ok(): pass\n"
FAILED_AND_SKIPPED = SKIPPED + "\ndef test_broken(): assert False\n"
XFAIL = "import pytest\n\n@pytest.mark.xfail(strict=True)\ndef test_known(): assert False\n"
MODULE_SKIPPED = "import pytest\n\npytest.skip('нет брокера', allow_module_level=True)\n"


def run_pytest(tmp_path: Path, source: str, *, fail_on_skip: str | None) -> subprocess.CompletedProcess[str]:
    (tmp_path / "test_case.py").write_text(source)
    # Пустой конфиг: addopts проекта (маркеры, этот же плагин) сюда не попадают.
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    env = {k: v for k, v in os.environ.items() if k not in {"PYTEST_FAIL_ON_SKIP", "PYTEST_ADDOPTS"}}
    env["PYTHONPATH"] = str(ROOT)
    if fail_on_skip is not None:
        env["PYTEST_FAIL_ON_SKIP"] = fail_on_skip
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "tests.strict_skips", "-p", "no:cacheprovider", "test_case.py"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize(
    ("source", "fail_on_skip", "expected"),
    [
        pytest.param(SKIPPED, "1", 1, id="skip-with-env-fails"),
        pytest.param(SKIPPED, None, 0, id="skip-without-env-passes"),
        pytest.param(SKIPPED, "0", 0, id="env-not-1-is-off"),
        pytest.param(MODULE_SKIPPED, "1", 1, id="module-skip-fails"),
        pytest.param(PASSED, "1", 0, id="no-skips-passes"),
        pytest.param(XFAIL, "1", 0, id="xfail-is-not-a-skip"),
        pytest.param(FAILED_AND_SKIPPED, "1", 1, id="failed-stays-failed"),
    ],
)
def test_exit_code(tmp_path: Path, source: str, fail_on_skip: str | None, expected: int) -> None:
    result = run_pytest(tmp_path, source, fail_on_skip=fail_on_skip)
    assert result.returncode == expected, result.stdout + result.stderr


def test_failure_names_the_skipped_test(tmp_path: Path) -> None:
    result = run_pytest(tmp_path, SKIPPED, fail_on_skip="1")
    assert "пропуск теста — провал" in result.stdout
    assert "test_case.py::test_skipped" in result.stdout
