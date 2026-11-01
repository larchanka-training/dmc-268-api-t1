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
    counts, gate_findings = build_summary.trivy_counts(fixture("trivy.json"))
    assert counts == {
        "CRITICAL": {"fixed": 1, "unfixed": 0},
        "HIGH": {"fixed": 0, "unfixed": 1},
        "MEDIUM": {"fixed": 1, "unfixed": 0},
        "LOW": {"fixed": 0, "unfixed": 2},
        "UNKNOWN": {"fixed": 0, "unfixed": 0},
    }
    assert gate_findings == [
        "CVE-2026-0001 (libssl3) — исправлено в 3.5.4-1, роняет job",
        "CVE-2026-0002 (libc6) — без исправления",
    ]


def test_trivy_unknown_severity_counts_as_unknown():
    counts, _ = build_summary.trivy_counts(
        {"Results": [{"Vulnerabilities": [vuln("CVE-1", "NEGLIGIBLE")]}]}
    )
    assert counts["UNKNOWN"] == {"fixed": 0, "unfixed": 1}


def test_render_trivy_table_and_unfixed_list():
    md = build_summary.render(fixture("metrics.json"), trivy=fixture("trivy.json"))
    assert "### trivy" in md
    assert "| CRITICAL — роняет job, если есть исправление | 1 | 0 | 1 |" in md
    assert "| HIGH — роняет job, если есть исправление | 0 | 1 | 1 |" in md
    assert "| LOW | 0 | 2 | 2 |" in md
    assert (
        "HIGH и CRITICAL: с исправлением роняют job, без исправления — нет "
        "(`ignore-unfixed`):\n\n"
        "- CVE-2026-0001 (libssl3) — исправлено в 3.5.4-1, роняет job\n"
        "- CVE-2026-0002 (libc6) — без исправления\n"
    ) in md
    # MEDIUM с исправлением job не роняет — в список не попадает
    assert "CVE-2026-0003" not in md


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
    md = build_summary.render(fixture("metrics.json"), trivy=report)
    assert "- CVE-2026-0010 (pkg) — без исправления" in md
    assert "CVE-2026-0011" not in md
    assert "- … и ещё 2" in md


# --- образ и база ---------------------------------------------------------


def test_render_with_base_shows_image_delta():
    md = build_summary.render(fixture("metrics.json"), base=fixture("base.json"))
    assert "Δ — к последнему успешному прогону CI на develop." in md
    assert "| Образ | `container.image.name` | `dmc268-api:ci` | |" in md
    assert "| ID | `container.image.id` | `sha256:0123456789ab` | |" in md
    assert "| `dmc268.build.image.size` | 242.23 MiB | +3.81 MiB (+1.6%) |" in md
    assert (
        "| Сжатый (`docker save` \\| gzip) | `dmc268.build.image.size.compressed`"
        " | 77.25 MiB | −976.6 KiB (−1.2%) |"
    ) in md


def test_render_without_base_shows_absolute_values():
    md = build_summary.render(fixture("metrics.json"))
    assert "Базы для сравнения нет" in md
    assert "| `dmc268.build.image.size` | 242.23 MiB | — |" in md
    assert "(+" not in md


def test_render_has_no_bundle_section():
    md = build_summary.render(fixture("metrics.json"))
    assert "### Бандл" not in md
    assert "bundle" not in md


def test_render_escapes_pipe_in_table_cells():
    metrics = {"image": {"name": "a|b", "size": 1, "compressed": 1}}
    findings = [{"code": "DL1", "level": "style", "line": 1, "message": "x|y"}]
    md = build_summary.render(metrics, hadolint=findings)
    assert "| Образ | `container.image.name` | `a\\|b` | |" in md
    assert "строка 1: x\\|y" in md


# --- hadolint -------------------------------------------------------------


def test_render_hadolint_levels_and_all_findings():
    md = build_summary.render(
        fixture("metrics.json"), hadolint=fixture("hadolint.json")
    )
    assert "| warning | 1 |" in md
    assert "| style — ниже порога job'а | 1 |" in md
    # в списке все замечания с уровнем — и уронившее job, и ниже порога
    assert "- `DL3008` (warning), строка 12: Pin versions in apt get install." in md
    assert (
        "- `DL3059` (style), строка 33: Multiple consecutive `RUN` instructions." in md
    )


def test_render_hadolint_truncates_findings():
    findings = [
        {"code": f"DL{i}", "level": "style", "line": i, "message": "m"}
        for i in range(12)
    ]
    md = build_summary.render(fixture("metrics.json"), hadolint=findings)
    assert "- `DL9` (style), строка 9: m" in md
    assert "`DL10`" not in md
    assert "- … и ещё 2" in md


def test_render_hadolint_lists_error_before_style():
    findings = [
        {"code": f"DL{i}", "level": "style", "line": i, "message": "m"}
        for i in range(10)
    ]
    findings.append({"code": "DL3000", "level": "error", "line": 99, "message": "e"})
    md = build_summary.render(fixture("metrics.json"), hadolint=findings)
    # error одиннадцатым в файле обрезка не прячет: уровни, что роняют job, — первыми
    assert "- `DL3000` (error), строка 99: e" in md
    assert md.index("`DL3000`") < md.index("`DL0`")
    assert "`DL9`" not in md
    assert "- … и ещё 1" in md


def test_render_clean_hadolint_is_one_line():
    md = build_summary.render(fixture("metrics.json"), hadolint=[])
    assert "### hadolint\n\nЗамечаний нет." in md


def test_render_without_scanner_reports_omits_sections():
    md = build_summary.render(fixture("metrics.json"))
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
        env=env,
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
    r = run_cli(
        "render",
        "--metrics", str(tmp_path / "нет.json"),
        "--base", str(FIXTURES / "base.json"),
        "--trivy", str(FIXTURES / "trivy.json"),
        "--hadolint", str(FIXTURES / "hadolint.json"),
    )  # fmt: skip
    assert r.returncode == 0, r.stderr
    assert r.stdout.startswith("## Summary сборки\n")
    assert "Метрики не собраны: образ не собрался или `collect` упал." in r.stdout
    assert "### trivy" in r.stdout
    assert "### hadolint" in r.stdout
    assert "### Образ" not in r.stdout
    assert "develop" not in r.stdout


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
    out.parent.mkdir()
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
