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
падение: summary пишется и после упавшего шага. Нет метрик — вместо раздела
образа строка о том, что они не собраны; trivy и hadolint выводятся всё равно. Разницу с develop считает по
`base.json` — метрикам последнего успешного прогона develop.

Порт `scripts/build-summary.mjs` фронтенда (dmc-268-ui-t1) без раздела бандла:
у бэкенда его нет. Ключи значений — по семантическим соглашениям
OpenTelemetry (`container.image.*`), чтобы отправить их в коллектор без
переименований; своё — в пространстве `dmc268.build.*`.
"""

import argparse
import json
import subprocess
import sys
import zlib
from collections.abc import Mapping
from pathlib import Path
from typing import IO, Any, cast

MAX_LISTED = 10
SEVERITIES = ("CRITICAL", "HIGH", "MEDIUM", "LOW", "UNKNOWN")
# Что роняет job: trivy — HIGH и CRITICAL с исправлением (ignore-unfixed),
# hadolint — всё от info и выше (failure-threshold по умолчанию у action).
TRIVY_GATE = ("CRITICAL", "HIGH")
HADOLINT_LEVELS = ("error", "warning", "info", "style")
HADOLINT_GATE = ("error", "warning", "info")

CHUNK = 1024 * 1024
NO_METRICS = "Метрики не собраны: образ не собрался или `collect` упал."

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
        stdout = cast(IO[bytes], proc.stdout)
        while chunk := stdout.read(CHUNK):
            total += len(gz.compress(chunk))
    # Выход из `with Popen` уже вызвал wait(): returncode выставлен.
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
    img = metrics["image"]
    bi = (base or {}).get("image", {})
    out = [
        "### Образ",
        "",
        "| Что | Ключ | Значение | Δ develop |",
        "|---|---|---|---|",
    ]
    out.append(f"| Образ | `container.image.name` | `{cell(img.get('name', ''))}` | |")
    if img.get("id"):
        out.append(f"| ID | `container.image.id` | `{cell(img['id'][:19])}` | |")
    size, compressed = img["size"], img["compressed"]
    out += [
        (
            f"| Размер | `dmc268.build.image.size` | {human_bytes(size)} "
            f"| {delta(size, bi.get('size'))} |"
        ),
        (
            "| Сжатый (`docker save` \\| gzip) | `dmc268.build.image.size.compressed` "
            f"| {human_bytes(compressed)} | {delta(compressed, bi.get('compressed'))} |"
        ),
    ]
    return [*out, ""]


def trivy_counts(
    report: Mapping[str, Any],
) -> tuple[dict[str, dict[str, int]], list[str]]:
    """Находки trivy по severity — с исправлением и без; список всех HIGH и
    CRITICAL: сначала с исправлением (они роняют job), потом без."""
    counts = {s: {"fixed": 0, "unfixed": 0} for s in SEVERITIES}
    gate_fixed: list[str] = []
    gate_unfixed: list[str] = []
    for result in report.get("Results") or []:
        for v in result.get("Vulnerabilities") or []:
            sev = v.get("Severity") if v.get("Severity") in SEVERITIES else "UNKNOWN"
            name = f"{v.get('VulnerabilityID')} ({v.get('PkgName')})"
            if fixed := v.get("FixedVersion"):
                counts[sev]["fixed"] += 1
                if sev in TRIVY_GATE:
                    gate_fixed.append(f"{name} — исправлено в {fixed}, роняет job")
            else:
                counts[sev]["unfixed"] += 1
                if sev in TRIVY_GATE:
                    gate_unfixed.append(f"{name} — без исправления")
    return counts, gate_fixed + gate_unfixed


def _listed(items: list[str]) -> list[str]:
    out = [f"- {item}" for item in items[:MAX_LISTED]]
    if len(items) > MAX_LISTED:
        out.append(f"- … и ещё {len(items) - MAX_LISTED}")
    return out


def _trivy_section(report: Mapping[str, Any] | None) -> list[str]:
    if report is None:
        return []
    counts, gate_findings = trivy_counts(report)
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
    if gate_findings:
        out += [
            "",
            (
                "HIGH и CRITICAL: с исправлением роняют job, без исправления — нет "
                "(`ignore-unfixed`):"
            ),
            "",
        ]
        out += _listed(gate_findings)
    return [*out, ""]


def _hadolint_section(findings: list[Any] | None) -> list[str]:
    if findings is None:
        return []
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
    out += [
        "",
        (
            "Порог job'а — `info`: замечания уровня `style` его не роняют. В списке — "
            "все замечания, и те, что уронили job, и те, что ниже порога."
        ),
        "",
    ]
    # Сначала уровни, что роняют job: иначе обрезка списка спрячет `error`
    # за десятком `style`. Сортировка устойчивая, неизвестный уровень — в конец.
    ordered = sorted(
        findings,
        key=lambda f: (
            HADOLINT_LEVELS.index(f.get("level"))
            if f.get("level") in HADOLINT_LEVELS
            else len(HADOLINT_LEVELS)
        ),
    )
    out += _listed(
        [
            f"`{f.get('code')}` ({f.get('level')}), строка {f.get('line')}: "
            f"{cell(f.get('message', ''))}"
            for f in ordered
        ]
    )
    return [*out, ""]


def render(
    metrics: Mapping[str, Any] | None,
    base: Mapping[str, Any] | None = None,
    trivy: Mapping[str, Any] | None = None,
    hadolint: list[Any] | None = None,
) -> str:
    out = ["## Summary сборки", ""]
    if metrics is not None:
        out += [
            "Δ — к последнему успешному прогону CI на develop."
            if base is not None
            else "Базы для сравнения нет: в кэше нет метрик develop (первый прогон "
            "или кэш устарел). Показаны абсолютные значения.",
            "",
            *_image_section(metrics, base),
        ]
    else:
        out += [NO_METRICS, ""]
    out += [
        *_trivy_section(trivy),
        *_hadolint_section(hadolint),
    ]
    return "\n".join(out)


# --- CLI ---------------------------------------------------------------------


def _read_json(path: str | None) -> Any:
    if not path:
        return None
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
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
        Path(args.out).write_text(
            json.dumps(metrics, indent=2) + "\n", encoding="utf-8"
        )
        return 0
    md = render(
        _read_json(args.metrics),
        base=_read_json(args.base),
        trivy=_read_json(args.trivy),
        hadolint=_read_json(args.hadolint),
    )
    sys.stdout.write(md + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
