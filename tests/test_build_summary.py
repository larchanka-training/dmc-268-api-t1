"""Summary сборки (`scripts/build_summary.py`): метрики образа и markdown.

Порт `scripts/build-summary.mjs` фронтенда без раздела бандла. Фикстуры —
`tests/fixtures/build_summary/`: отчёты trivy (`--format json`) и hadolint
(`--format json`), метрики образа и база develop. `collect` гоняется настоящим
`python3` с подложным `docker` на PATH: настоящий демон в тесте не нужен, а
контракт CLI — тот, что вызывает CI.
"""

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from scripts import build_summary

FIXTURES = Path("tests/fixtures/build_summary")
SCRIPT = Path("scripts/build_summary.py")

ENV = {
    "GITHUB_WORKFLOW": "CI",
    "GITHUB_RUN_ID": "42",
    "GITHUB_SERVER_URL": "https://github.com",
    "GITHUB_REPOSITORY": "larchanka-training/dmc-268-api-t1",
    "GITHUB_JOB": "image",
    "GITHUB_SHA": "a" * 40,
}


def fixture(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def vuln(
    vuln_id: str, severity: str, fixed: str | None = None, pkg: str = "pkg"
) -> dict[str, Any]:
    v = {"VulnerabilityID": vuln_id, "PkgName": pkg, "Severity": severity}
    if fixed:
        v["FixedVersion"] = fixed
    return v


# --- форматирование -------------------------------------------------------


@pytest.mark.parametrize(
    ("n", "expected"),
    [
        (0, "0 B"),
        (512, "512 B"),
        (2048, "2.0 KiB"),
        (3 * 1024 * 1024, "3.00 MiB"),
        (2 * 1024**3, "2.00 GiB"),
    ],
)
def test_human_bytes(n, expected):
    assert build_summary.human_bytes(n) == expected


@pytest.mark.parametrize(
    ("now", "before", "expected"),
    [
        (100, None, "—"),
        (100, 100, "0"),
        (110, 100, "+10 B (+10.0%)"),
        (90, 100, "−10 B (−10.0%)"),
        (100, 0, "+100 B"),
    ],
)
def test_delta(now, before, expected):
    assert build_summary.delta(now, before) == expected


# --- trivy ----------------------------------------------------------------


def test_trivy_counts_fixed_and_unfixed_per_severity():
    counts, unfixed_gate = build_summary.trivy_counts(fixture("trivy.json"))
    assert counts == {
        "CRITICAL": {"fixed": 1, "unfixed": 0},
        "HIGH": {"fixed": 0, "unfixed": 1},
        "MEDIUM": {"fixed": 1, "unfixed": 0},
        "LOW": {"fixed": 0, "unfixed": 2},
        "UNKNOWN": {"fixed": 0, "unfixed": 0},
    }
    assert unfixed_gate == ["CVE-2026-0002 (libc6)"]


def test_trivy_unknown_severity_counts_as_unknown():
    counts, _ = build_summary.trivy_counts(
        {"Results": [{"Vulnerabilities": [vuln("CVE-1", "NEGLIGIBLE")]}]}
    )
    assert counts["UNKNOWN"] == {"fixed": 0, "unfixed": 1}


def test_render_trivy_table_and_unfixed_list():
    md = build_summary.render(
        fixture("metrics.json"), trivy=fixture("trivy.json"), env=ENV
    )
    assert "### trivy" in md
    assert "| CRITICAL — роняет job, если есть исправление | 1 | 0 | 1 |" in md
    assert "| HIGH — роняет job, если есть исправление | 0 | 1 | 1 |" in md
    assert "| LOW | 0 | 2 | 2 |" in md
    assert "HIGH и CRITICAL без исправления (job не роняют, `ignore-unfixed`):" in md
    assert "- CVE-2026-0002 (libc6)" in md
    # с исправлением в список не попадает: его роняет сам trivy
    assert "CVE-2026-0001" not in md


def test_render_trivy_truncates_unfixed_list():
    report = {
        "Results": [
            {
                "Vulnerabilities": [
                    vuln(f"CVE-2026-{i:04}", "HIGH") for i in range(1, 13)
                ]
            }
        ]
    }
    md = build_summary.render(fixture("metrics.json"), trivy=report, env=ENV)
    assert "- CVE-2026-0010 (pkg)" in md
    assert "CVE-2026-0011" not in md
    assert "- … и ещё 2" in md


# --- образ и база ---------------------------------------------------------


def test_render_with_base_shows_image_delta():
    md = build_summary.render(
        fixture("metrics.json"), base=fixture("base.json"), env=ENV
    )
    assert "Δ — к последнему успешному прогону CI на develop." in md
    assert "| Образ | `container.image.name` | `dmc268-api:ci` | |" in md
    assert "| ID | `container.image.id` | `sha256:0123456789ab` | |" in md
    assert "| `dmc268.build.image.size` | 242.23 MiB | +3.81 MiB (+1.6%) |" in md
    assert (
        "| Сжатый (`docker save` \\| gzip) | `dmc268.build.image.size.compressed`"
        " | 77.25 MiB | −976.6 KiB (−1.2%) |"
    ) in md


def test_render_without_base_shows_absolute_values():
    md = build_summary.render(fixture("metrics.json"), env=ENV)
    assert "Базы для сравнения нет" in md
    assert "| `dmc268.build.image.size` | 242.23 MiB | — |" in md
    assert "(+" not in md


def test_render_has_no_bundle_section():
    md = build_summary.render(fixture("metrics.json"), env=ENV)
    assert "### Бандл" not in md
    assert "bundle" not in md


def test_render_pipeline_section():
    md = build_summary.render(fixture("metrics.json"), env=ENV)
    assert md.startswith("## Summary сборки\n")
    assert "| Workflow | `cicd.pipeline.name` | CI |" in md
    assert (
        "| Прогон | `cicd.pipeline.run.id` | "
        "[42](https://github.com/larchanka-training/dmc-268-api-t1/actions/runs/42) |"
    ) in md
    assert (
        "`cicd.pipeline.task.name` | `image` (`cicd.pipeline.task.type`: `build`)" in md
    )
    assert "| Коммит | `vcs.ref.head.revision` | `aaaaaaaaaaaa` |" in md


def test_render_escapes_pipe_in_table_cells():
    metrics = {"image": {"name": "a|b", "size": 1}}
    md = build_summary.render(metrics, env={**ENV, "GITHUB_WORKFLOW": "C|I"})
    assert "| `cicd.pipeline.name` | C\\|I |" in md
    assert "`a\\|b`" in md


# --- hadolint -------------------------------------------------------------


def test_render_hadolint_levels_and_findings_below_gate():
    md = build_summary.render(
        fixture("metrics.json"), hadolint=fixture("hadolint.json"), env=ENV
    )
    assert "| warning | 1 |" in md
    assert "| style — ниже порога job'а | 1 |" in md
    assert "- `DL3059`, строка 33: Multiple consecutive `RUN` instructions." in md
    # warning роняет job, его hadolint и так показал — в список не попадает
    assert "- `DL3008`" not in md


def test_render_hadolint_truncates_findings_below_gate():
    findings = [
        {"code": f"DL{i}", "level": "style", "line": i, "message": "m"}
        for i in range(12)
    ]
    md = build_summary.render(fixture("metrics.json"), hadolint=findings, env=ENV)
    assert "- `DL9`, строка 9: m" in md
    assert "`DL10`" not in md
    assert "- … и ещё 2" in md


def test_render_clean_hadolint_is_one_line():
    md = build_summary.render(fixture("metrics.json"), hadolint=[], env=ENV)
    assert "### hadolint\n\nЗамечаний нет." in md


def test_render_without_scanner_reports_omits_sections():
    md = build_summary.render(fixture("metrics.json"), env=ENV)
    assert "### trivy" not in md
    assert "### hadolint" not in md


# --- CLI ------------------------------------------------------------------


def run_cli(
    *args: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        env=env if env is not None else {**os.environ, **ENV},
        check=False,
    )


def test_cli_render_from_fixtures():
    r = run_cli(
        "render",
        "--metrics", str(FIXTURES / "metrics.json"),
        "--base", str(FIXTURES / "base.json"),
        "--trivy", str(FIXTURES / "trivy.json"),
        "--hadolint", str(FIXTURES / "hadolint.json"),
    )  # fmt: skip
    assert r.returncode == 0, r.stderr
    assert (
        r.stdout
        == build_summary.render(
            fixture("metrics.json"),
            base=fixture("base.json"),
            trivy=fixture("trivy.json"),
            hadolint=fixture("hadolint.json"),
            env=ENV,
        )
        + "\n"
    )


def test_cli_render_skips_missing_and_broken_optional_files(tmp_path):
    broken = tmp_path / "trivy.json"
    broken.write_text("{не json", encoding="utf-8")
    r = run_cli(
        "render",
        "--metrics", str(FIXTURES / "metrics.json"),
        "--base", str(tmp_path / "нет.json"),
        "--trivy", str(broken),
    )  # fmt: skip
    assert r.returncode == 0, r.stderr
    assert "Базы для сравнения нет" in r.stdout
    assert "### trivy" not in r.stdout
    assert "`dmc268-api:ci`" in r.stdout


def test_cli_render_without_metrics(tmp_path):
    r = run_cli("render", "--metrics", str(tmp_path / "нет.json"))
    assert r.returncode == 0
    assert r.stdout == (
        "## Summary сборки\n\nМетрики не собраны: образ не собрался или `collect` упал.\n"
    )


def test_cli_unknown_command():
    r = run_cli("publish")
    assert r.returncode == 2
    assert r.stdout == ""
    assert "usage" in r.stderr


# Подложный docker: inspect отдаёт размер и id, save — 1 МиБ нулей
# (сжимается почти в ноль), с SAVE_FAIL — падает; вызовы — в журнал.
FAKE_DOCKER = """#!/bin/sh
echo "$*" >> "$CALLS"
case "$*" in
  "image inspect --format {{.Size}} "*) echo 254000000 ;;
  "image inspect --format {{.Id}} "*) echo sha256:feed ;;
  "save "*) [ -n "$SAVE_FAIL" ] && exit 1; head -c 1048576 /dev/zero ;;
  *) exit 1 ;;
esac
"""


@pytest.fixture
def fake_docker(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text(FAKE_DOCKER, encoding="utf-8")
    docker.chmod(0o755)
    calls = tmp_path / "calls.log"
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "CALLS": str(calls),
    }
    return env, calls


def test_cli_collect_writes_image_metrics(tmp_path, fake_docker):
    env, calls = fake_docker
    out = tmp_path / "build-summary" / "metrics.json"
    r = run_cli("collect", "--image", "dmc268-api:ci", "--out", str(out), env=env)
    assert r.returncode == 0, r.stderr
    text = out.read_text(encoding="utf-8")
    assert text.endswith("}\n")
    image = json.loads(text)["image"]
    assert {k: image[k] for k in ("name", "id", "size")} == {
        "name": "dmc268-api:ci",
        "id": "sha256:feed",
        "size": 254000000,
    }
    # gzip мегабайта нулей — около килобайта, но не ноль
    assert 0 < image["compressed"] < 10_000
    assert "save dmc268-api:ci" in calls.read_text(encoding="utf-8").splitlines()


@pytest.mark.parametrize(
    "broken",
    [{"SAVE_FAIL": "1"}, {"PATH": "/nonexistent"}],
    ids=["docker-save-fails", "no-docker"],
)
def test_cli_collect_fails_without_writing_metrics(tmp_path, fake_docker, broken):
    env, _ = fake_docker
    out = tmp_path / "metrics.json"
    r = run_cli(
        "collect", "--image", "dmc268-api:ci", "--out", str(out), env={**env, **broken}
    )
    assert r.returncode == 1
    assert "Traceback" not in r.stderr
    assert not out.exists()
