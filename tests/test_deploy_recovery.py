"""Деплой после сбоя: восстановление состояния OpenTofu и сверка вершины develop.

`recover-state.sh` срабатывает раз в долгое время и именно тогда, когда всё
уже сломано, — узнавать о его поломке деплоем нельзя. Поэтому он гоняется
настоящим bash с подложными `tofu`, `aws`, `timeout` и туннелем, а вход —
настоящий: `tests/fixtures/deploy/errored-tfstate.json` (`errored.tfstate`; `*.tfstate` в .gitignore) и `tflock.json` — то, что
OpenTofu 1.13.2 оставил на docker:29-dind, когда RustFS 1.0.3 остановили
посреди apply (serial и lineage — из прогона, ресурсы убраны: скрипт их не читает). `Who` блокировки
тест подставляет свой: скрипт снимает только блокировку этого раннера.

Туннель, его сторож и `check-tip.sh` — тоже настоящим bash с подложными `curl`,
`ssh`, `pkill` и `gh`.
"""

import json
import os
import re
import socket
import subprocess
from pathlib import Path

import pytest
import yaml

FIXTURES = Path("tests/fixtures/deploy")
SCRIPTS = Path(".github/scripts")
DEPLOY_WORKFLOW = Path(".github/workflows/deploy.yml")
LOCK_ID = "7313173c-96ec-0c6e-cb77-92fab8b342c6"

# Подложный tofu: вызовы — в журнал, `state push` падает, если велит тест.
FAKE_TOFU = """#!/usr/bin/env bash
echo "tofu $*" >> "$CALLS"
case "$*" in
  *"state push"*) exit "${PUSH_EXIT:-0}" ;;
  *force-unlock*) exit "${UNLOCK_EXIT:-0}" ;;
esac
"""

# Подложный aws: get-object кладёт блокировку из LOCK_JSON или отвечает, как
# S3 без объекта; AWS_FAIL — хранилище ответило ошибкой.
FAKE_AWS = """#!/usr/bin/env bash
echo "aws $*" >> "$CALLS"
if [ -n "${AWS_FAIL:-}" ]; then echo "$AWS_FAIL" >&2; exit 255; fi
out="${!#}"
if [ -n "${LOCK_JSON:-}" ]; then cp "$LOCK_JSON" "$out"; exit 0; fi
echo "An error occurred (NoSuchKey) when calling the GetObject operation: The specified key does not exist." >&2
exit 255
"""

# coreutils timeout на macOS нет: подложный пишет вызов и запускает команду.
FAKE_TIMEOUT = """#!/usr/bin/env bash
echo "timeout $1" >> "$CALLS"
shift
exec "$@"
"""

FAKE_TUNNEL = """#!/usr/bin/env bash
echo "tunnel $* wait=${S3_TUNNEL_WAIT:-}" >> "$CALLS"
exit "${TUNNEL_EXIT:-0}"
"""


def write_bin(tmp_path: Path, **bodies: str) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    for name, body in bodies.items():
        path = bin_dir / name
        path.write_text(body)
        path.chmod(0o755)
    return bin_dir


def own_lock(tmp_path: Path, who: str | None = None) -> Path:
    """Блокировка из фикстуры с Who этого раннера (или заданным)."""
    me = f"{subprocess.check_output(['id', '-un'], text=True).strip()}@{socket.gethostname()}"
    info = json.loads((FIXTURES / "tflock.json").read_text())
    info["Who"] = who or me
    path = tmp_path / "lock.json"
    path.write_text(json.dumps(info))
    return path


def recover(
    tmp_path: Path, *, errored: bool = True, init: bool = True, **env: str
) -> tuple[subprocess.CompletedProcess[str], dict[str, str], Path]:
    """Прогнать recover-state.sh в копии infra/; вернуть результат, итог для summary и корень."""
    root = tmp_path / "repo"
    (root / "infra").mkdir(parents=True, exist_ok=True)
    if init:
        (root / "infra" / ".terraform").mkdir(exist_ok=True)
        backend = {"backend": {"type": "s3", "config": {
            "bucket": "dmc268-tfstate", "key": "api/terraform.tfstate",
            "endpoints": {"s3": "http://127.0.0.1:9000"},
        }}}
        (root / "infra" / ".terraform" / "terraform.tfstate").write_text(json.dumps(backend))
    if errored:
        (root / "infra" / "errored.tfstate").write_text((FIXTURES / "errored-tfstate.json").read_text())
    bin_dir = write_bin(tmp_path, tofu=FAKE_TOFU, aws=FAKE_AWS, timeout=FAKE_TIMEOUT)
    tunnel = write_bin(tmp_path, tunnel=FAKE_TUNNEL) / "tunnel"
    report = tmp_path / "report"
    result = subprocess.run(
        ["bash", str((SCRIPTS / "deploy" / "recover-state.sh").resolve())],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
        env={
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "REPORT_DIR": str(report),
            "CALLS": str(tmp_path / "calls"),
            "S3_TUNNEL": str(tunnel),
            "RECOVER_WAIT": "0",
            **env,
        },
    )
    pairs = dict(line.split("=", 1) for line in (report / "state-recovery").read_text().splitlines())
    return result, pairs, root


def calls(tmp_path: Path) -> list[str]:
    path = tmp_path / "calls"
    return path.read_text().splitlines() if path.exists() else []


def tofu_calls(tmp_path: Path) -> list[str]:
    return [c for c in calls(tmp_path) if c.startswith("tofu ")]


# --- восстановление состояния -----------------------------------------------


def test_recovery_pushes_the_errored_state_under_its_lock_then_unlocks(tmp_path: Path) -> None:
    result, pairs, root = recover(tmp_path, LOCK_JSON=str(own_lock(tmp_path)))

    assert result.returncode == 0, result.stdout + result.stderr
    assert tofu_calls(tmp_path) == [
        "tofu -chdir=infra state push -no-color -lock=false errored.tfstate",
        f"tofu -chdir=infra force-unlock -no-color -force {LOCK_ID}",
    ]
    assert pairs == {"lock": LOCK_ID, "serial": "4", "result": "restored"}
    assert not (root / "infra" / "errored.tfstate").exists(), "в файле состояния пароли: записан — убрать"


def test_recovery_reopens_the_tunnel_and_reads_the_lock_next_to_the_state(tmp_path: Path) -> None:
    recover(tmp_path, LOCK_JSON=str(own_lock(tmp_path)))

    log = calls(tmp_path)
    assert log[0] == "tunnel 9000 wait=0"
    get = next(c for c in log if c.startswith("aws "))
    assert "--endpoint-url http://127.0.0.1:9000 s3api get-object" in get
    assert "--bucket dmc268-tfstate --key api/terraform.tfstate.tflock" in get


def test_recovery_without_a_lock_takes_one_for_the_push(tmp_path: Path) -> None:
    """Блокировка снялась, а запись — нет: push берёт блокировку, как обычно."""
    result, pairs, _ = recover(tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    assert tofu_calls(tmp_path) == ["tofu -chdir=infra state push -no-color -lock-timeout=2m errored.tfstate"]
    assert pairs == {"serial": "4", "result": "restored"}


def test_recovery_releases_a_lock_left_without_errored_state(tmp_path: Path) -> None:
    """Состояние записалось, связь пропала до снятия блокировки."""
    result, pairs, _ = recover(tmp_path, errored=False, LOCK_JSON=str(own_lock(tmp_path)))

    assert result.returncode == 0, result.stdout + result.stderr
    assert tofu_calls(tmp_path) == [f"tofu -chdir=infra force-unlock -no-color -force {LOCK_ID}"]
    assert pairs == {"lock": LOCK_ID, "result": "unlocked"}


def test_recovery_with_nothing_left_changes_nothing(tmp_path: Path) -> None:
    result, pairs, _ = recover(tmp_path, errored=False)

    assert result.returncode == 0, result.stdout + result.stderr
    assert tofu_calls(tmp_path) == []
    assert pairs == {"result": "none"}


def test_recovery_leaves_a_foreign_lock_alone_when_nothing_is_unsaved(tmp_path: Path) -> None:
    """План упал, не дождавшись чужой блокировки: этот запуск ничего не сломал."""
    result, pairs, _ = recover(tmp_path, errored=False, LOCK_JSON=str(own_lock(tmp_path, who="dev@laptop")))

    assert result.returncode == 0, result.stdout + result.stderr
    assert tofu_calls(tmp_path) == []
    assert pairs == {"lock": LOCK_ID, "who": "dev@laptop", "result": "foreign"}


def test_recovery_never_touches_a_lock_of_another_run(tmp_path: Path) -> None:
    result, pairs, root = recover(tmp_path, LOCK_JSON=str(own_lock(tmp_path, who="runner@other-vm")))

    assert result.returncode == 1
    assert tofu_calls(tmp_path) == []
    assert pairs["result"] == "failed"
    assert "runner@other-vm" in pairs["reason"]
    assert "::error::состояние OpenTofu не восстановлено" in result.stdout
    assert (root / "infra" / "errored.tfstate").exists()


def test_recovery_keeps_the_lock_when_the_push_is_refused(tmp_path: Path) -> None:
    """state push без -force отказывает на более новом состоянии: блокировку тогда не снимаем."""
    result, pairs, root = recover(tmp_path, LOCK_JSON=str(own_lock(tmp_path)), PUSH_EXIT="1")

    assert result.returncode == 1
    assert not any("force-unlock" in c for c in tofu_calls(tmp_path))
    assert all("-force " not in c for c in tofu_calls(tmp_path) if "state push" in c)
    assert pairs["result"] == "failed"
    assert (root / "infra" / "errored.tfstate").exists()


def test_recovery_waits_for_a_hung_storage_longer_than_a_plain_tunnel(tmp_path: Path) -> None:
    """Ответа хранилища ждёт туннель (#211) — сколько велит RECOVER_WAIT, по умолчанию 120 с."""
    recover(tmp_path, RECOVER_WAIT="120", LOCK_JSON=str(own_lock(tmp_path)))

    assert calls(tmp_path)[0] == "tunnel 9000 wait=120"


def test_recovery_does_not_guess_on_a_storage_error(tmp_path: Path) -> None:
    result, pairs, _ = recover(tmp_path, AWS_FAIL="Could not connect to the endpoint URL")

    assert result.returncode == 1
    assert tofu_calls(tmp_path) == []
    assert "блокировку не прочитать" in pairs["reason"]


def test_recovery_reports_a_failed_tunnel(tmp_path: Path) -> None:
    result, pairs, _ = recover(tmp_path, TUNNEL_EXIT="1")

    assert result.returncode == 1
    assert tofu_calls(tmp_path) == []
    assert pairs["reason"] == "туннель не открылся или хранилище не ответило за 0 с"


def test_recovery_before_init_has_nothing_to_restore(tmp_path: Path) -> None:
    result, pairs, _ = recover(tmp_path, errored=False, init=False)

    assert result.returncode == 0, result.stdout + result.stderr
    assert calls(tmp_path) == []
    assert pairs == {"result": "none"}


def test_recovery_runs_store_commands_under_a_timeout(tmp_path: Path) -> None:
    """Зависшее хранилище не должно держать и сам шаг восстановления."""
    recover(tmp_path, LOCK_JSON=str(own_lock(tmp_path)))

    timeouts = [c for c in calls(tmp_path) if c.startswith("timeout ")]
    assert len(timeouts) == 3, calls(tmp_path)


def test_manual_recovery_instruction_exists() -> None:
    """::error:: и summary отсылают к разделу README — он должен быть."""
    readme = Path("infra/README.md").read_text()
    assert "### Если деплой упал посреди apply" in readme
    section = readme.split("### Если деплой упал посреди apply", 1)[1]
    for command in ("tofu force-unlock", "tofu state push", ".tflock"):
        assert command in section, command


# --- сторож туннеля -----------------------------------------------------------

# Подложный curl: коды по очереди из CURL_CODES, последний повторяется.
QUEUE_CURL = """#!/usr/bin/env bash
n=$(( $(cat "$COUNT" 2>/dev/null || echo 0) + 1 )); echo "$n" > "$COUNT"
read -ra codes <<< "$CURL_CODES"
i=$(( n <= ${#codes[@]} ? n - 1 : ${#codes[@]} - 1 ))
exit "${codes[$i]}"
"""

FAKE_PKILL = """#!/usr/bin/env bash
echo "pkill $*" >> "$CALLS"
"""


def watchdog(tmp_path: Path, codes: str) -> subprocess.CompletedProcess[str]:
    bin_dir = write_bin(tmp_path, curl=QUEUE_CURL, pkill=FAKE_PKILL)
    return subprocess.run(
        ["bash", str(SCRIPTS / "s3-watchdog.sh"), "9000"],
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
        env={
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "CALLS": str(tmp_path / "calls"),
            "COUNT": str(tmp_path / "count"),
            "CURL_CODES": codes,
            "S3_WATCHDOG_LIMIT": "0",
            "S3_WATCHDOG_INTERVAL": "0",
        },
    )


def test_watchdog_closes_the_tunnel_to_a_hung_storage(tmp_path: Path) -> None:
    result = watchdog(tmp_path, "0 28")

    assert result.returncode == 0
    assert calls(tmp_path) == [r"pkill -f ^ssh .* -L 9000:127\.0\.0\.1:9000 stand$"]
    assert "туннель закрыт" in result.stdout


def test_watchdog_leaves_a_refused_connection_to_tofu(tmp_path: Path) -> None:
    """Отказ в соединении — не зависание: OpenTofu падает на нём сам."""
    watchdog(tmp_path, "7 7 52 28")

    assert (tmp_path / "count").read_text().strip() == "4"
    assert len(calls(tmp_path)) == 1


def tunnel_command() -> str:
    """Команда туннеля из s3-tunnel.sh так, как её видит pkill -f."""
    script = (SCRIPTS / "s3-tunnel.sh").read_text()
    (line,) = re.findall(r"^ssh .* -L \"\$forward\" stand$", script, re.MULTILINE)
    return str(line).replace('"$forward"', "9000:127.0.0.1:9000")


def test_watchdog_and_reopening_find_the_tunnel_they_open() -> None:
    """Шаблоны pkill должны совпасть с туннелем: иначе повторное открытие упадёт на занятом порте."""
    watchdog_script = (SCRIPTS / "s3-watchdog.sh").read_text()
    assert 'pkill -f "^ssh .* -L ${forward} stand\\$"' in watchdog_script
    forward = "9000:127\\.0\\.0\\.1:9000"
    assert re.fullmatch(rf"^ssh .* -L {forward} stand$", tunnel_command())
    tunnel_script = (SCRIPTS / "s3-tunnel.sh").read_text()
    assert 'pkill -f "^ssh .* -L ${forward//./\\\\.} stand\\$"' in tunnel_script


def test_storage_tunnel_tolerates_a_longer_outage_than_the_master() -> None:
    assert "-o ServerAliveCountMax=12" in tunnel_command()


# Подложный curl туннеля: коды ответа по очереди из CODES, последний — дальше.
FAKE_PROBE = """#!/usr/bin/env bash
echo "curl $*" >> "$CALLS"
n=$(cat "$COUNT" 2>/dev/null || echo 0)
echo $((n + 1)) > "$COUNT"
read -ra codes <<< "$CODES"
i=$(( n < ${#codes[@]} ? n : ${#codes[@]} - 1 ))
printf '%s' "${codes[$i]}"
"""
RECORD = """#!/usr/bin/env bash
echo "$(basename "$0") $*" >> "$CALLS"
"""


def open_tunnel(tmp_path: Path, codes: str, wait: str = "30") -> subprocess.CompletedProcess[str]:
    bin_dir = write_bin(tmp_path, curl=FAKE_PROBE, ssh=RECORD, pkill=RECORD, nohup=RECORD, sleep=RECORD)
    return subprocess.run(
        # Порт, который на машине разработчика никто не слушает: проверка, что
        # прежний туннель закрылся, идёт по настоящему /dev/tcp.
        ["bash", str(SCRIPTS / "s3-tunnel.sh"), "59997"],
        capture_output=True,
        text=True,
        check=False,
        env={
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "CALLS": str(tmp_path / "calls"),
            "COUNT": str(tmp_path / "count"),
            "CODES": codes,
            "S3_TUNNEL_WAIT": wait,
            "REPORT_DIR": str(tmp_path / "report"),
        },
    )


def test_tunnel_waits_for_the_storage_to_answer(tmp_path: Path) -> None:
    """Порт на петле открыт сразу — его слушает ssh; готовность — первый ответ HTTP хранилища (#211)."""
    result = open_tunnel(tmp_path, "000 000 403")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "хранилище отвечает (HTTP 403)" in result.stdout
    assert (tmp_path / "count").read_text().strip() == "3"


def test_tunnel_fails_when_the_storage_never_answers(tmp_path: Path) -> None:
    """ssh поднял проброс, а хранилище молчит: шаг падает здесь, а не потом на `tofu init`."""
    result = open_tunnel(tmp_path, "000", wait="0")

    assert result.returncode == 1
    assert "::error::туннель поднялся, но хранилище за ним не ответило за 0 с" in result.stdout
    assert not any(c.startswith("nohup ") for c in calls(tmp_path))


# --- сверка вершины -------------------------------------------------------------

FAKE_GH = """#!/usr/bin/env bash
echo "gh $*" >> "$CALLS"
[ -n "${GH_FAIL:-}" ] && { echo "HTTP 502" >&2; exit 1; }
echo "$TIP"
"""


def check_tip(tmp_path: Path, **env: str) -> tuple[subprocess.CompletedProcess[str], str, Path]:
    bin_dir = write_bin(tmp_path, gh=FAKE_GH)
    output = tmp_path / "output"
    output.touch()
    report = tmp_path / "report"
    result = subprocess.run(
        ["bash", str(SCRIPTS / "deploy" / "check-tip.sh")],
        capture_output=True,
        text=True,
        check=False,
        env={
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "CALLS": str(tmp_path / "calls"),
            "GITHUB_OUTPUT": str(output),
            "GITHUB_REPOSITORY": "o/r",
            "REPORT_DIR": str(report),
            "SHA": "a" * 40,
            **env,
        },
    )
    return result, output.read_text(), report


def test_tip_still_at_the_commit_deploys(tmp_path: Path) -> None:
    result, output, report = check_tip(tmp_path, TIP="a" * 40)

    assert result.returncode == 0, result.stderr
    assert output == "deploy=true\n"
    assert calls(tmp_path) == ["gh api repos/o/r/commits/develop --jq .sha"]
    assert not (report / "stale-tip").exists()


def test_stale_rerun_is_skipped_without_failing(tmp_path: Path) -> None:
    result, output, report = check_tip(tmp_path, TIP="b" * 40)

    assert result.returncode == 0, result.stderr
    assert output == "deploy=false\n"
    assert f"::notice::{'a' * 40} уже не вершина develop ({'b' * 40})" in result.stdout
    assert (report / "stale-tip").read_text().strip() == "b" * 40


@pytest.mark.parametrize("env", [{"GH_FAIL": "1", "TIP": ""}, {"TIP": ""}])
def test_unknown_tip_does_not_deploy(tmp_path: Path, env: dict[str, str]) -> None:
    result, output, _ = check_tip(tmp_path, **env)

    assert result.returncode != 0
    assert "deploy=" not in output


def deploy_steps() -> list[dict[str, object]]:
    workflow = yaml.safe_load(DEPLOY_WORKFLOW.read_text())
    return list(workflow["jobs"]["deploy"]["steps"])


FRESH = "steps.tip.outputs.deploy == 'true'"


def test_every_step_after_the_tip_check_waits_for_it() -> None:
    """Пропуск устаревшего коммита останавливает весь выкат, а не его часть."""
    steps = deploy_steps()
    ids = [str(s["id"]) for s in steps]
    after = steps[ids.index("tip") + 1:]
    unguarded = {str(s["id"]): s.get("if") for s in after if s.get("if") != FRESH}
    # Остальные идут только после шага, который сам ждёт сверки: без него их
    # условие ложно.
    assert set(unguarded) == {"recover", "api_logs", "prune", "summary"}, unguarded
    assert unguarded["summary"] == "always()"
    for step_id in ("recover", "api_logs", "prune"):
        assert re.search(r"steps\.(infra|plan|apply)\.outcome == '(failure|success)'", str(unguarded[step_id]))


def test_tip_check_comes_before_anything_touches_the_stand() -> None:
    ids = [str(s["id"]) for s in deploy_steps()]
    assert ids[: ids.index("tip") + 1] == ["start", "checkout", "tip"]
    tip = deploy_steps()[2]
    assert tip["env"] == {"GH_TOKEN": "${{ github.token }}", "SHA": "${{ needs.gate.outputs.sha }}"}


def test_recovery_runs_after_every_step_that_locks_the_state() -> None:
    recover_step = next(s for s in deploy_steps() if s["id"] == "recover")
    condition = str(recover_step["if"])
    assert condition.startswith("failure() && (")
    for step_id in ("infra", "plan", "apply"):
        assert f"steps.{step_id}.outcome == 'failure'" in condition


def test_scripts_called_from_the_workflow_are_executable() -> None:
    """deploy.yml зовёт скрипты по пути, без bash: без бита x шаг упадёт на Permission denied."""
    called = set(re.findall(r"\.github/scripts/[\w/.-]+\.sh", DEPLOY_WORKFLOW.read_text()))
    assert called
    for path in sorted(called):
        assert os.access(path, os.X_OK), path


def test_state_commands_wait_for_a_short_foreign_lock() -> None:
    infra = next(s for s in deploy_steps() if s["id"] == "infra")
    assert "-lock-timeout=2m" in str(infra["run"])
    for script in ("plan.sh", "apply.sh"):
        command = next(
            line for line in (SCRIPTS / "deploy" / script).read_text().splitlines()
            if line.startswith("tofu -chdir=infra ") and (" plan " in line or " apply " in line)
        )
        assert "-lock-timeout=2m" in command, script
