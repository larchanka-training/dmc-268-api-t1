"""Summary job'а `image` в `$GITHUB_STEP_SUMMARY`: размер образа, находки trivy
и замечания hadolint; summary job'а `compose-smoke`: холодный старт стека.

Три команды, только стандартная библиотека (скрипт зовут простым `python3`,
вне `uv`):

    python3 scripts/build_summary.py collect --image dmc268-api:ci \\
        --out build-summary/metrics.json
    python3 scripts/build_summary.py render --metrics metrics.json \\
        [--base base.json] [--trivy trivy.json] [--hadolint hadolint.json] \\
        | tee -a "$GITHUB_STEP_SUMMARY"
    python3 scripts/build_summary.py startup --up-ms 41234 \\
        --api-started-at 2026-11-16T10:00:00.123456789Z --api-logs api.log \\
        | tee -a "$GITHUB_STEP_SUMMARY"

Вывод — через `tee -a`, а не `>>`: таблица остаётся и в логе job, ведь
summary шага через API не отдаётся, а базовую линию собирают по логам.

`collect` берёт у `docker image inspect` размер и id образа, а сжатый размер
считает по потоку `docker save | gzip` — образ целиком в память не читается.
`render` собирает markdown и не падает на отсутствующих файлах: summary
пишется и после упавшего шага. Нет или битый отчёт сканера — раздел с
заголовком и строкой, что отчёта нет; нет метрик — вместо раздела образа
строка о том, что они не собраны; нет базы — значения без разницы. Разницу с
develop считает по `base.json` — метрикам последней успешной сборки образа на
develop.

`startup` — холодный старт на чистом раннере: длительность `docker compose up
-d --wait` (меряет шаг CI) и старт API — от `State.StartedAt` контейнера до
записи uvicorn «Application startup complete.» в его логе с метками времени
(`docker compose logs --timestamps`). Обе метки ставит Docker, поэтому цифра
не зависит от частоты опроса. Нет значения — в ячейке прочерк, команда не
падает: summary не должен ронять job.

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
from collections.abc import Iterable, Mapping
from datetime import datetime
from pathlib import Path
from typing import IO, Any, cast

SEVERITIES = ("CRITICAL", "HIGH", "MEDIUM", "LOW", "UNKNOWN")
# Что роняет job: trivy — HIGH и CRITICAL с исправлением (ignore-unfixed),
# hadolint — всё, кроме style (failure-threshold info по умолчанию у action).
TRIVY_GATE = ("CRITICAL", "HIGH")
HADOLINT_LEVELS = ("error", "warning", "info", "style")

# Сообщение uvicorn о готовности приложения: lifespan завершился, порт слушается.
STARTUP_MARK = "Application startup complete."

CHUNK = 1024 * 1024
NO_METRICS = "Метрики не собраны: образ не собрался или `collect` упал."
NO_REPORT = "Отчёта нет: шаг сканера упал."

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
    size, compressed = img["size"], img["compressed"]
    out += [
        f"| Образ | `container.image.name` | `{cell(img['name'])}` | |",
        f"| ID | `container.image.id` | `{cell(img['id'][:19])}` | |",
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


def _trivy_section(report: Mapping[str, Any] | None) -> list[str]:
    if report is None:
        return ["### trivy", "", NO_REPORT, ""]
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
        out += [f"- {item}" for item in gate_findings]
    return [*out, ""]


def _hadolint_section(findings: list[Any] | None) -> list[str]:
    out = ["### hadolint", ""]
    if findings is None:
        return [*out, NO_REPORT, ""]
    if not findings:
        return [*out, "Замечаний нет.", ""]
    out += [
        "| Уровень `dmc268.build.lint.level` | Замечаний `dmc268.build.lint.findings` |",
        "|---|---|",
    ]
    for level in HADOLINT_LEVELS:
        n = sum(1 for f in findings if f.get("level") == level)
        if n:
            below = " — ниже порога job'а" if level == "style" else ""
            out.append(f"| {level}{below} | {n} |")
    out += [
        "",
        (
            "Порог job'а — `info`: замечания уровня `style` его не роняют. В списке — "
            "все замечания, и те, что уронили job, и те, что ниже порога."
        ),
        "",
    ]
    out += [
        f"- `{f.get('code')}` ({f.get('level')}), строка {f.get('line')}: "
        f"{cell(f.get('message', ''))}"
        for f in findings
    ]
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
            "Δ — к последней успешной сборке образа на develop."
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


# --- холодный старт -----------------------------------------------------------


def parse_docker_time(value: str) -> datetime | None:
    """Время Docker (`2026-11-16T10:00:00.123456789Z`) или None, если формат чужой.

    `fromisoformat` с Python 3.11 принимает `Z` и дробную часть любой длины,
    лишнее после микросекунд отбрасывает. Время без зоны и голая дата — не
    метка Docker: None.
    """
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def api_startup_seconds(started_at: str, log_lines: Iterable[str]) -> float | None:
    """Секунды от запуска контейнера до первой записи о готовности после него.

    Строки — вывод `docker compose logs --timestamps --no-log-prefix`: метка
    времени, пробел, запись. Записи до `started_at` (прошлый запуск контейнера)
    не считаются. Нет запуска или записи — None.
    """
    start = parse_docker_time(started_at)
    if start is None:
        return None
    for line in log_lines:
        stamp, _, record = line.partition(" ")
        if STARTUP_MARK not in record:
            continue
        at = parse_docker_time(stamp)
        if at is not None and at >= start:
            return (at - start).total_seconds()
    return None


def seconds(value: float | None) -> str:
    return "—" if value is None else f"{value:.1f} с"


def render_startup(up_seconds: float | None, api_seconds: float | None) -> str:
    """Раздел summary `compose-smoke`: холодный старт стека и API."""
    return "\n".join(
        [
            "## Холодный старт",
            "",
            (
                "Чистый раннер: образы собираются и скачиваются заново. Цифры "
                "информационные, порога нет — раннеры GitHub шумят."
            ),
            "",
            "| Что | Ключ | Значение |",
            "|---|---|---|",
            (
                "| `docker compose up -d --wait` — сборка, скачивание, все сервисы готовы "
                f"| `dmc268.build.startup.compose` | {seconds(up_seconds)} |"
            ),
            (
                "| API: запуск контейнера → «Application startup complete.» "
                f"| `dmc268.build.startup.api` | {seconds(api_seconds)} |"
            ),
            "",
        ]
    )


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
    st = sub.add_parser("startup", help="markdown холодного старта в stdout")
    st.add_argument("--up-ms", type=int, help="длительность docker compose up, мс")
    st.add_argument("--api-started-at", help="State.StartedAt контейнера api")
    st.add_argument("--api-logs", help="docker compose logs --timestamps api")
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
    if args.command == "startup":
        up = None if args.up_ms is None else args.up_ms / 1000
        api = None
        if args.api_started_at and args.api_logs:
            try:
                lines = Path(args.api_logs).read_text(encoding="utf-8").splitlines()
            except OSError:
                lines = []
            api = api_startup_seconds(args.api_started_at, lines)
        sys.stdout.write(render_startup(up, api) + "\n")
        return 0
    # Отчёт не той формы — то же, что битый файл: строка «Отчёта нет», а не падение.
    trivy = _read_json(args.trivy)
    hadolint = _read_json(args.hadolint)
    md = render(
        _read_json(args.metrics),
        base=_read_json(args.base),
        trivy=trivy if isinstance(trivy, dict) else None,
        hadolint=hadolint if isinstance(hadolint, list) else None,
    )
    sys.stdout.write(md + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
