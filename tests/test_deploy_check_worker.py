"""Проверка воркеров после выката (`.github/scripts/deploy/check-worker.sh`) по каждому экземпляру.

Сколько экземпляров, задаёт `worker_replicas` (#99): скрипт находит их на сервере по имени
(`dmc268-worker`, `dmc268-worker-<N>`) и ждёт, что все запущены и 30 с не перезапускаются.
Скрипт гоняется настоящим bash с подложными `ssh`, `date` и `sleep`: часы — файл, `sleep`
двигает их, поэтому полторы минуты опроса проходят мгновенно.
"""

import os
import subprocess
from pathlib import Path

SCRIPT = Path(".github/scripts/deploy/check-worker.sh").resolve()

# Подложный ssh: `docker ps` отдаёт NAMES, `docker inspect` — INSPECT, где @N@ — номер
# опроса (так растёт счётчик рестартов), `docker logs` — строку с именем. Команды — в журнал.
FAKE_SSH = """#!/usr/bin/env bash
while [ $# -gt 0 ]; do case "$1" in -o) shift 2 ;; -*) shift ;; *) break ;; esac; done
shift
echo "$*" >> "$CALLS"
case "$*" in
  *"docker ps -a"*) printf '%b\\n' "${NAMES:-}" ;;
  *"docker inspect"*)
    n=$(( $(cat "$CALLS.polls" 2>/dev/null || echo 0) + 1 )); echo "$n" > "$CALLS.polls"
    printf '%b\\n' "${INSPECT//@N@/$n}" ;;
  *"docker logs"*) echo "лог ${*##*--tail 50 }" ;;
esac
"""

FAKE_DATE = """#!/usr/bin/env bash
cat "$CLOCK"
"""

FAKE_SLEEP = """#!/usr/bin/env bash
echo $(( $(cat "$CLOCK") + $1 )) > "$CLOCK"
"""


def check(tmp_path: Path, **env: str) -> subprocess.CompletedProcess[str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in (("ssh", FAKE_SSH), ("date", FAKE_DATE), ("sleep", FAKE_SLEEP)):
        path = bin_dir / name
        path.write_text(body)
        path.chmod(0o755)
    clock = tmp_path / "clock"
    clock.write_text("1000\n")
    return subprocess.run(
        ["bash", str(SCRIPT)],
        capture_output=True,
        text=True,
        check=False,
        env={
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "CALLS": str(tmp_path / "calls"),
            "CLOCK": str(clock),
            **env,
        },
    )


def test_every_worker_replica_is_checked(tmp_path: Path) -> None:
    result = check(
        tmp_path,
        TF_VAR_worker_replicas="2",
        NAMES=r"dmc268-worker\ndmc268-worker-2\ndmc268-worker-old",
        INSPECT=r"/dmc268-worker true 0\n/dmc268-worker-2 true 3",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "воркеры работают 30 с без рестартов (2 шт.)" in result.stdout
    assert "  dmc268-worker: 0\n  dmc268-worker-2: 3\n" in result.stdout
    inspected = [c for c in (tmp_path / "calls").read_text().splitlines() if "docker inspect" in c]
    # Чужое имя с тем же началом под проверку не попадает.
    assert all("dmc268-worker dmc268-worker-2 " in c and "dmc268-worker-old" not in c for c in inspected)


def test_one_worker_by_default(tmp_path: Path) -> None:
    """Без TF_VAR_worker_replicas — один экземпляр, как дефолт переменной в infra."""
    result = check(tmp_path, NAMES="dmc268-worker", INSPECT="/dmc268-worker true 2")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "(1 шт.)" in result.stdout


def test_a_missing_replica_fails_the_deploy(tmp_path: Path) -> None:
    result = check(
        tmp_path, TF_VAR_worker_replicas="2", NAMES="dmc268-worker", INSPECT="/dmc268-worker true 0"
    )

    assert result.returncode == 1
    assert "::error::воркеры не проработали 30 с без рестартов за полторы минуты: ждали 2 шт., на сервере — 1" in (
        result.stdout
    )
    assert "лог dmc268-worker" in result.stdout
    assert "; нет: dmc268-worker-2" in result.stdout


def test_a_leftover_replica_does_not_stand_in_for_a_missing_one(tmp_path: Path) -> None:
    """Число сходится, имена нет: оставшийся от ручного запуска `dmc268-worker-3` не заменяет
    недостающий `dmc268-worker-2`."""
    result = check(
        tmp_path,
        TF_VAR_worker_replicas="2",
        NAMES=r"dmc268-worker\ndmc268-worker-3",
        INSPECT=r"/dmc268-worker true 0\n/dmc268-worker-3 true 0",
    )

    assert result.returncode == 1
    assert "ждали 2 шт., на сервере — 2 (dmc268-worker dmc268-worker-3 ); нет: dmc268-worker-2; лишние: dmc268-worker-3" in (
        result.stdout
    )


def test_no_worker_at_all_names_every_missing_one(tmp_path: Path) -> None:
    result = check(tmp_path, TF_VAR_worker_replicas="2", NAMES="", INSPECT="")

    assert result.returncode == 1
    assert "на сервере — 0 ( ); нет: dmc268-worker dmc268-worker-2" in result.stdout


def test_a_restarting_replica_fails_the_deploy(tmp_path: Path) -> None:
    """Первый работает, второй перезапускается на каждом опросе: окно не набирается."""
    result = check(
        tmp_path,
        TF_VAR_worker_replicas="2",
        NAMES=r"dmc268-worker\ndmc268-worker-2",
        INSPECT=r"/dmc268-worker true 0\n/dmc268-worker-2 true @N@",
    )

    assert result.returncode == 1
    assert "--- dmc268-worker-2: последние строки лога" in result.stdout
    assert "лог dmc268-worker-2" in result.stdout


def test_a_stopped_replica_fails_the_deploy(tmp_path: Path) -> None:
    result = check(
        tmp_path,
        TF_VAR_worker_replicas="2",
        NAMES=r"dmc268-worker\ndmc268-worker-2",
        INSPECT=r"/dmc268-worker true 0\n/dmc268-worker-2 false 0",
    )

    assert result.returncode == 1


def test_an_extra_replica_fails_the_deploy(tmp_path: Path) -> None:
    """Экземпляров больше, чем ждали: на сервере остался неснятый — проверка не врёт про число."""
    result = check(
        tmp_path,
        TF_VAR_worker_replicas="1",
        NAMES=r"dmc268-worker\ndmc268-worker-2",
        INSPECT=r"/dmc268-worker true 0\n/dmc268-worker-2 true 0",
    )

    assert result.returncode == 1
    assert "ждали 1 шт., на сервере — 2" in result.stdout
    assert "; лишние: dmc268-worker-2" in result.stdout
