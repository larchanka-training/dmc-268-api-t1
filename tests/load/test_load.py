"""Полный нагрузочный прогон с отчётом и порогами - вручную, workflow `load.yml`.

Маркер `load`: основной прогон его исключает. Профиль и пороги приходят
окружением, отчёт - `load.json` и `load.md` в `LOAD_REPORT_DIR`, Markdown
дописывается в сводку job GitHub Actions.

Окружение (все необязательны): `LOAD_PULL_REQUESTS`, `LOAD_REDELIVERIES`,
`LOAD_SENDERS`, `LOAD_WORKERS`, `LOAD_MODEL_LATENCY_SECONDS`,
`LOAD_GITHUB_LATENCY_SECONDS`, `LOAD_TIMEOUT_SECONDS`; пороги -
`LOAD_MAX_INTAKE_P95_MS` и `LOAD_MAX_END_TO_END_P95_SECONDS`.
"""

import os
from pathlib import Path

import pytest

from ..conftest import requires_broker, requires_db
from ..e2e.stack import Stack
from .checks import assert_consistent
from .scenario import LoadProfile, report_json, report_markdown, run_load, summarize

pytestmark = [pytest.mark.load, requires_db, requires_broker]


def _env(name: str, default: float) -> float:
    return float(os.environ.get(name) or default)


def test_load_run_with_a_report(stack: Stack, tmp_path: Path) -> None:
    defaults = LoadProfile()
    profile = LoadProfile(
        pull_requests=int(_env("LOAD_PULL_REQUESTS", 200)),
        redeliveries=int(_env("LOAD_REDELIVERIES", 40)),
        senders=int(_env("LOAD_SENDERS", 32)),
        workers=int(_env("LOAD_WORKERS", 8)),
        model_latency_seconds=_env("LOAD_MODEL_LATENCY_SECONDS", 0.5),
        github_latency_seconds=_env("LOAD_GITHUB_LATENCY_SECONDS", 0.05),
        timeout_seconds=_env("LOAD_TIMEOUT_SECONDS", 600),
        seed=defaults.seed,
    )

    result = run_load(stack, profile)

    out = Path(os.environ.get("LOAD_REPORT_DIR") or tmp_path)
    out.mkdir(parents=True, exist_ok=True)
    markdown = report_markdown(result)
    (out / "load.md").write_text(markdown, encoding="utf-8")
    (out / "load.json").write_text(report_json(result), encoding="utf-8")
    if summary_path := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(summary_path, "a", encoding="utf-8") as handle:
            handle.write(markdown)
    print(f"\nотчёт нагрузки: {out}\n\n{markdown}")

    summary = summarize(result)
    assert_consistent(summary)
    if limit := os.environ.get("LOAD_MAX_INTAKE_P95_MS"):
        p95 = summary["intake_latency_seconds"]["p95"] * 1000
        assert p95 <= float(limit), f"p95 ответа на вебхук {p95:.0f} мс > {limit} мс"
    if limit := os.environ.get("LOAD_MAX_END_TO_END_P95_SECONDS"):
        p95 = summary["end_to_end_seconds"]["p95"]
        assert p95 <= float(limit), f"p95 до завершения прогона {p95:.1f} с > {limit} с"
