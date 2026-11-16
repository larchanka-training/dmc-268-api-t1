"""Стенд не запускается в демо-режиме (change `demo-mode`, спека `backend-delivery`)."""

import subprocess
from pathlib import Path

import pytest
import yaml

from tests.infra_text import read_infra_text

DEPLOY_WORKFLOW = Path(".github/workflows/deploy.yml")
STEP = "Отклонить демо-режим"


def _gate_steps() -> list[dict[str, object]]:
    workflow = yaml.safe_load(DEPLOY_WORKFLOW.read_text())
    return list(workflow["jobs"]["gate"]["steps"])


def _check(demo_mode: str) -> subprocess.CompletedProcess[str]:
    [step] = [s for s in _gate_steps() if s.get("name") == STEP]
    return subprocess.run(
        ["bash", "-c", str(step["run"])],
        env={"DEMO_MODE": demo_mode, "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize("value", ["true", "TRUE", "True"])
def test_deploy_rejects_demo_mode(value: str) -> None:
    result = _check(value)

    assert result.returncode == 1
    assert "::error::DEMO_MODE=true" in result.stdout


@pytest.mark.parametrize("value", ["", "false", "0"])
def test_deploy_passes_without_demo_mode(value: str) -> None:
    assert _check(value).returncode == 0


def test_rejection_runs_first_before_anything_is_built() -> None:
    assert _gate_steps()[0]["name"] == STEP


def test_infra_does_not_pass_demo_mode_to_the_stand() -> None:
    assert "DEMO_MODE" not in read_infra_text()
