"""Summary job'а `image` в `$GITHUB_STEP_SUMMARY`: размер образа, находки trivy
и замечания hadolint.

Две команды, только стандартная библиотека (скрипт зовут простым `python3`,
вне `uv`):

    python3 scripts/build_summary.py collect --image dmc268-api:ci \\
        --out build-summary/metrics.json
    python3 scripts/build_summary.py render --metrics metrics.json \\
        [--base base.json] [--trivy trivy.json] [--hadolint hadolint.json] \\
        >> "$GITHUB_STEP_SUMMARY"

`collect` берёт у `docker image inspect` размер и id образа, а сжатый размер
считает по потоку `docker save | gzip` — образ целиком в память не читается.
`render` собирает markdown; нет файла сканера или базы — нет и раздела, а не
падение: summary пишется и после упавшего шага. Разницу с develop считает по
`base.json` — метрикам последнего успешного прогона develop.

Порт `scripts/build-summary.mjs` фронтенда (dmc-268-ui-t1) без раздела бандла:
у бэкенда его нет. Ключи значений — по семантическим соглашениям
OpenTelemetry (`cicd.pipeline.*`, `container.image.*`, `vcs.ref.*`), чтобы
отправить их в коллектор без переименований; своё — в пространстве
`dmc268.build.*`.
"""

import argparse
import json
import os
import subprocess
import sys
import zlib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

MAX_LISTED = 10
SEVERITIES = ("CRITICAL", "HIGH", "MEDIUM", "LOW", "UNKNOWN")
# Что роняет job: trivy — HIGH и CRITICAL с исправлением (ignore-unfixed),
# hadolint — всё от info и выше (failure-threshold по умолчанию у action).
TRIVY_GATE = ("CRITICAL", "HIGH")
HADOLINT_LEVELS = ("error", "warning", "info", "style")
HADOLINT_GATE = ("error", "warning", "info")

CHUNK = 1024 * 1024
NO_METRICS = (
    "## Summary сборки\n\nМетрики не собраны: образ не собрался или `collect` упал.\n"
)

# --- метрики -----------------------------------------------------------------


def _inspect(image: str, field: str) -> str:
    return subprocess.run(
        ["docker", "image", "inspect", "--format", f"{{{{.{field}}}}}", image],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def _compressed_size(image: str) -> int:
    """Байт в `docker save IMAGE | gzip -c`: поток сжимается кусками."""
    # wbits 31 — формат gzip, уровень 6 — как у `gzip` по умолчанию.
    gz = zlib.compressobj(6, zlib.DEFLATED, 31)
    total = 0
    with subprocess.Popen(["docker", "save", image], stdout=subprocess.PIPE) as proc:
        # Не assert: под `python -O` проверка исчезла бы.
        if proc.stdout is None:
            raise RuntimeError("docker save не дал поток stdout")
        while chunk := proc.stdout.read(CHUNK):
            total += len(gz.compress(chunk))
    if proc.returncode != 0:
        raise subprocess.CalledProcessError(proc.returncode, proc.args)
    return total + len(gz.flush())


def collect(image: str) -> dict[str, Any]:
    return {
        "image": {
            "name": image,
            "id": _inspect(image, "Id"),
            "size": int(_inspect(image, "Size")),
            "compressed": _compressed_size(image),
        }
    }


# --- форматирование ----------------------------------------------------------


def human_bytes(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KiB"
    if n < 1024 * 1024 * 1024:
        return f"{n / 1024 / 1024:.2f} MiB"
    return f"{n / 1024 / 1024 / 1024:.2f} GiB"


def delta(now: int, before: int | None) -> str:
    if before is None:
        return "—"
    d = now - before
    if d == 0:
        return "0"
    sign = "+" if d > 0 else "−"
    pct = "" if before == 0 else f" ({sign}{abs(d) / before * 100:.1f}%)"
    return f"{sign}{human_bytes(abs(d))}{pct}"


def cell(value: object) -> str:
    """Значение для ячейки таблицы: `|` разорвал бы строку на столбцы."""
    return str(value).replace("|", "\\|").replace("\n", " ")


# --- разделы -----------------------------------------------------------------


def _image_section(
    metrics: Mapping[str, Any], base: Mapping[str, Any] | None
) -> list[str]:
    img = metrics.get("image")
    if not isinstance(img, Mapping):
        return []
    bi = base.get("image") if isinstance(base, Mapping) else None
    bi = bi if isinstance(bi, Mapping) else {}
    out = [
        "### Образ",
        "",
        "| Что | Ключ | Значение | Δ develop |",
        "|---|---|---|---|",
    ]
    out.append(f"| Образ | `container.image.name` | `{cell(img.get('name', ''))}` | |")
    if img.get("id"):
        out.append(f"| ID | `container.image.id` | `{cell(img['id'][:19])}` | |")
    out.append(
        f"| Размер | `dmc268.build.image.size` | {human_bytes(img['size'])} "
        f"| {delta(img['size'], bi.get('size'))} |"
    )
    if img.get("compressed"):
        out.append(
            "| Сжатый (`docker save` \\| gzip) | `dmc268.build.image.size.compressed` "
            f"| {human_bytes(img['compressed'])} "
            f"| {delta(img['compressed'], bi.get('compressed'))} |"
        )
    return [*out, ""]


def trivy_counts(
    report: Mapping[str, Any],
) -> tuple[dict[str, dict[str, int]], list[str]]:
    """Находки trivy по severity — с исправлением и без; список HIGH и
    CRITICAL без исправления (`ID (пакет)`)."""
    counts = {s: {"fixed": 0, "unfixed": 0} for s in SEVERITIES}
    unfixed_gate: list[str] = []
    for result in report.get("Results") or []:
        for v in result.get("Vulnerabilities") or []:
            sev = v.get("Severity") if v.get("Severity") in SEVERITIES else "UNKNOWN"
            if v.get("FixedVersion"):
                counts[sev]["fixed"] += 1
            else:
                counts[sev]["unfixed"] += 1
                if sev in TRIVY_GATE:
                    unfixed_gate.append(
                        f"{v.get('VulnerabilityID')} ({v.get('PkgName')})"
                    )
    return counts, unfixed_gate


def _listed(items: list[str]) -> list[str]:
    out = [f"- {item}" for item in items[:MAX_LISTED]]
    if len(items) > MAX_LISTED:
        out.append(f"- … и ещё {len(items) - MAX_LISTED}")
    return out


def _trivy_section(report: Mapping[str, Any] | None) -> list[str]:
    if not isinstance(report, Mapping):
        return []
    counts, unfixed_gate = trivy_counts(report)
    out = [
        "### trivy",
        "",
        (
            "| Severity `dmc268.build.scan.severity` | С исправлением "
            "| Без исправления (`unfixed`) | Всего `dmc268.build.scan.vulnerabilities` |"
        ),
        "|---|---|---|---|",
    ]
    for s in SEVERITIES:
        fixed, unfixed = counts[s]["fixed"], counts[s]["unfixed"]
        gate = " — роняет job, если есть исправление" if s in TRIVY_GATE else ""
        out.append(f"| {s}{gate} | {fixed} | {unfixed} | {fixed + unfixed} |")
    if unfixed_gate:
        out += [
            "",
            "HIGH и CRITICAL без исправления (job не роняют, `ignore-unfixed`):",
            "",
        ]
        out += _listed(unfixed_gate)
    return [*out, ""]


def _hadolint_section(findings: list[Any] | None) -> list[str]:
    if not isinstance(findings, list):
        return []
    # Файл пишет внешний action: элемент не той формы пропускаем, а не падаем.
    findings = [f for f in findings if isinstance(f, Mapping)]
    out = ["### hadolint", ""]
    if not findings:
        return [*out, "Замечаний нет.", ""]
    out += [
        "| Уровень `dmc268.build.lint.level` | Замечаний `dmc268.build.lint.findings` |",
        "|---|---|",
    ]
    for level in HADOLINT_LEVELS:
        n = sum(1 for f in findings if f.get("level") == level)
        if n:
            below = "" if level in HADOLINT_GATE else " — ниже порога job'а"
            out.append(f"| {level}{below} | {n} |")
    out += ["", "Порог job'а — `info`: замечания уровня `style` его не роняют.", ""]
    below_gate = [f for f in findings if f.get("level") not in HADOLINT_GATE]
    out += _listed(
        [
            f"`{f.get('code')}`, строка {f.get('line')}: {cell(f.get('message', ''))}"
            for f in below_gate
        ]
    )
    return [*out, ""]


def _pipeline_section(env: Mapping[str, str]) -> list[str]:
    run = env.get("GITHUB_RUN_ID", "")
    server, repo = env.get("GITHUB_SERVER_URL"), env.get("GITHUB_REPOSITORY")
    url = f"{server}/{repo}/actions/runs/{run}" if server and repo else ""
    return [
        "| Что | Ключ | Значение |",
        "|---|---|---|",
        f"| Workflow | `cicd.pipeline.name` | {cell(env.get('GITHUB_WORKFLOW', ''))} |",
        f"| Прогон | `cicd.pipeline.run.id` | {f'[{run}]({url})' if url else run} |",
        (
            f"| Job | `cicd.pipeline.task.name` | `{cell(env.get('GITHUB_JOB', ''))}` "
            "(`cicd.pipeline.task.type`: `build`) |"
        ),
        f"| Коммит | `vcs.ref.head.revision` | `{env.get('GITHUB_SHA', '')[:12]}` |",
        "",
    ]


def render(
    metrics: Mapping[str, Any],
    base: Mapping[str, Any] | None = None,
    trivy: Mapping[str, Any] | None = None,
    hadolint: list[Any] | None = None,
    env: Mapping[str, str] | None = None,
) -> str:
    out = ["## Summary сборки", "", *_pipeline_section(env or {})]
    out += [
        "Δ — к последнему успешному прогону CI на develop."
        if isinstance(base, Mapping)
        else "Базы для сравнения нет: в кэше нет метрик develop (первый прогон или кэш "
        "устарел). Показаны абсолютные значения.",
        "",
    ]
    out += [
        *_image_section(metrics, base),
        *_trivy_section(trivy),
        *_hadolint_section(hadolint),
    ]
    return "\n".join(out)


# --- CLI ---------------------------------------------------------------------


def _read_json(path: str | None) -> Any:
    if not path:
        return None
    # Два except, а не кортеж: `ruff format` под py314 снимает скобки
    # (PEP 758), а скрипт зовут системным python3 старше 3.14.
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError:
        return None
    except ValueError:
        return None


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="build_summary.py", description=__doc__.splitlines()[0]
    )
    sub = parser.add_subparsers(dest="command", required=True)
    c = sub.add_parser("collect", help="метрики образа в json")
    c.add_argument("--image", required=True)
    c.add_argument("--out", required=True)
    r = sub.add_parser("render", help="markdown summary в stdout")
    r.add_argument("--metrics", required=True)
    r.add_argument("--base")
    r.add_argument("--trivy")
    r.add_argument("--hadolint")
    return parser


def main(argv: list[str]) -> int:
    args = _parser().parse_args(argv)
    if args.command == "collect":
        try:
            metrics = collect(args.image)
        except (OSError, subprocess.CalledProcessError, ValueError) as exc:
            print(f"collect: не удалось снять метрики образа: {exc}", file=sys.stderr)
            return 1
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(metrics, indent=2) + "\n", encoding="utf-8")
        return 0
    metrics = _read_json(args.metrics)
    if not isinstance(metrics, Mapping):
        sys.stdout.write(NO_METRICS)
        return 0
    md = render(
        metrics,
        base=_read_json(args.base),
        trivy=_read_json(args.trivy),
        hadolint=_read_json(args.hadolint),
        env=os.environ,
    )
    sys.stdout.write(md + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
