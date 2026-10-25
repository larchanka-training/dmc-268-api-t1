"""Плагин pytest: пропущенный тест — провал, если `PYTEST_FAIL_ON_SKIP=1`.

Пропуск без базы или брокера задуман для локальной работы (см. докстринг
`tests/conftest.py`), поэтому без переменной плагин ничего не делает. CI
выставляет её в шаге pytest: там пропуск значит, что прогон проверил меньше,
чем кажется, — обычно не дошла переменная окружения или сервис. Локально
правило включается той же переменной.

Считаются пропуски и тестов, и модулей при сборке (`allow_module_level`).
`xfail` пропуском не считается, deselect по `-m` — тоже.
"""

import os

import pytest

ENV_VAR = "PYTEST_FAIL_ON_SKIP"


class StrictSkips:
    def __init__(self) -> None:
        self.skipped: list[str] = []

    def _record(self, report: pytest.CollectReport | pytest.TestReport) -> None:
        if report.skipped and not hasattr(report, "wasxfail"):
            self.skipped.append(report.nodeid)

    def pytest_collectreport(self, report: pytest.CollectReport) -> None:
        self._record(report)

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        self._record(report)

    def pytest_sessionfinish(self, session: pytest.Session, exitstatus: int) -> None:
        # Уже упавший прогон не перекрашиваем: его код точнее. «Тестов не
        # собрано» (5) при пропуске — это пропуск всего модуля при сборке.
        if self.skipped and exitstatus in (pytest.ExitCode.OK, pytest.ExitCode.NO_TESTS_COLLECTED):
            session.exitstatus = pytest.ExitCode.TESTS_FAILED

    def pytest_terminal_summary(self, terminalreporter: pytest.TerminalReporter) -> None:
        if not self.skipped:
            return
        message = (
            f"{ENV_VAR}=1, пропущено: {len(self.skipped)} — прогон считается упавшим. "
            "Прогон проверил меньше, чем кажется: обычно не задана TEST_DATABASE_URL, "
            "TEST_RABBITMQ_URL, TEST_RABBITMQ_CONTAINER или ключ EUrouter; причина "
            "каждого пропуска — в выводе с -rs."
        )
        terminalreporter.write_sep("=", "пропуск теста — провал", red=True, bold=True)
        terminalreporter.write_line(message, red=True)
        for nodeid in self.skipped:
            terminalreporter.write_line(f"  {nodeid}")
        if os.environ.get("GITHUB_ACTIONS") == "true":
            # Аннотация прогона: видна в summary, не надо читать лог.
            terminalreporter.write_line(f"::error::{message}")


def pytest_configure(config: pytest.Config) -> None:
    if os.environ.get(ENV_VAR) == "1":
        config.pluginmanager.register(StrictSkips(), "strict-skips")
