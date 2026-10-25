"""Скрипт поднятия хранилища состояния: смена образа не теряет хранилище.

Ветка смены образа — единственная в деплое, которая удаляет контейнер с
состоянием OpenTofu. Ошибка порядка (удалить, а потом не скачать) оставила бы
стенд без хранилища, и узнали бы мы об этом деплоем. Поэтому скрипт гоняется
здесь с поддельным `docker`: заглушка пишет вызовы в журнал и отвечает так,
как велит тест.
"""

import subprocess
from pathlib import Path

RENDER = Path(".github/scripts/render-s3-script.sh")
OLD = "rustfs/rustfs:1.0.0"
NEW = "rustfs/rustfs:1.0.3"

# Заглушка: `inspect` отдаёт образ и состояние из переменных окружения, после
# успешного `rm` контейнера нет. Команды из FAIL падают, остальные успешны.
# Каждый вызов — строка в журнале.
FAKE_DOCKER = """#!/usr/bin/env bash
echo "$*" >> "$DOCKER_LOG"
for cmd in $FAIL; do
    [ "$1" = "$cmd" ] && { echo "$cmd сломан" >&2; exit 1; }
done
[ "$1" = rm ] && touch "$DOCKER_LOG.removed"
if [ "$1" = inspect ] && [ "$4" = dmc268-s3 ] && [ -e "$DOCKER_LOG.removed" ]; then
    exit 1
fi
if [ "$1" = inspect ]; then
    case "$3" in
        *Config.Image*) echo "$CURRENT_IMAGE" ;;
        *State.Running*) echo "$RUNNING" ;;
        *) echo "sha256:0" ;;
    esac
fi
exit 0
"""


def run_script(tmp_path: Path, *, fail: str = "") -> tuple[str, list[str]]:
    """Выход скрипта и журнал вызовов docker: контейнер запущен на OLD, нужен NEW."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text(FAKE_DOCKER)
    docker.chmod(0o755)
    log = tmp_path / "docker.log"
    log.touch()

    script = subprocess.run(
        ["sh", str(RENDER), "dmc268-s3", "dmc268-s3-data", "9000", NEW],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    result = subprocess.run(
        ["bash", "-c", script],
        input="RUSTFS_ACCESS_KEY=k\nRUSTFS_SECRET_KEY=s\n",
        capture_output=True,
        text=True,
        env={
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "DOCKER_LOG": str(log),
            "CURRENT_IMAGE": OLD,
            "RUNNING": "true",
            "FAIL": fail,
        },
        check=False,
    )
    calls = [line.split()[0] for line in log.read_text().splitlines()]
    return result.stdout, calls


def test_new_image_pulled_before_old_container_removed(tmp_path: Path) -> None:
    out, calls = run_script(tmp_path)

    assert f"S3_IMAGE=обновлён: {OLD} -> {NEW}" in out
    assert calls.index("pull") < calls.index("stop") < calls.index("rm") < calls.index("run")


def test_failed_pull_keeps_old_container(tmp_path: Path) -> None:
    out, calls = run_script(tmp_path, fail="pull")

    assert f"S3_IMAGE=не скачался {NEW}, остаётся {OLD}: pull сломан" in out
    assert "S3=уже запущен" in out
    assert "stop" not in calls
    assert "rm" not in calls


def test_failed_removal_reported_not_claimed_as_update(tmp_path: Path) -> None:
    out, _ = run_script(tmp_path, fail="rm")

    assert f"S3_IMAGE=не удалось убрать старый контейнер, остаётся {OLD}: rm сломан" in out
    assert "обновлён" not in out
