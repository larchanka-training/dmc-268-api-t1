"""SSH к стенду в деплое: одно соединение на все вызовы и туннели мимо него.

`.github/actions/stand-ssh` пишет хост `stand` с ControlMaster: через фоновое
соединение идут `ssh stand …` шагов и запросы Docker к `ssh://stand` (#203).
Туннель через master ведёт себя иначе: проброс открывает сам master,
`ExitOnForwardFailure` на занятом порте не срабатывает, а туннель закрывается
вместе с master по ControlPersist простоя (проверено на OpenSSH 9.6). Поэтому
каждый `ssh -L` в `.github/` обязан идти своим соединением —
`-o ControlPath=none`. Новый туннель без него молча потерял бы проверку
проброса; ловим здесь, а не деплоем.

Скрипт action гоняется настоящим bash с подложным HOME: так проверяется и
heredoc внутри YAML, и то, что конфиг вообще пишется.
"""

import os
import re
import subprocess
from pathlib import Path

import yaml

ACTION = Path(".github/actions/stand-ssh/action.yml")
GITHUB_DIR = Path(".github")
HOST = "203.0.113.7"

# sun_path unix-сокета — 104 байта на macOS, 108 на Linux, с завершающим нулём.
SOCKET_PATH_LIMIT = 103
RUNNER_HOME = "/home/runner"
# %C — SHA1 в hex; пока master поднимается, ssh держит сокет под временным
# именем с суффиксом из точки и 16 символов.
CONTROL_HASH_LEN = 40
TEMP_SUFFIX_LEN = 17


def stand_ssh_script() -> str:
    """Скрипт единственного шага action stand-ssh."""
    action = yaml.safe_load(ACTION.read_text())
    (step,) = action["runs"]["steps"]
    return str(step["run"])


def host_options(config: str, host: str) -> dict[str, str]:
    """Опции блока `Host <host>` из ssh_config: ключи в нижнем регистре."""
    block = re.search(rf"^Host {re.escape(host)}\n((?:[ \t]+\S.*\n?)*)", config, re.MULTILINE)
    if block is None:
        return {}
    pairs = (line.split(None, 1) for line in block.group(1).splitlines() if line.strip())
    return {key.lower(): value.strip() for key, value in pairs}


def expanded_socket_length(control_path: str, home: str) -> int:
    """Длина пути сокета на раннере: ~ — домашний каталог, %C — хеш, плюс временный суффикс."""
    path = control_path.replace("~", home, 1).replace("%C", "x" * CONTROL_HASH_LEN)
    return len(path) + TEMP_SUFFIX_LEN


def tunnels_without_own_connection(text: str) -> list[str]:
    """Вызовы `ssh … -L …`, которые пошли бы через ControlMaster."""
    commands = re.findall(
        r"^[ \t]*(?:- )?(?:run: )?(ssh\b[^\n]*\s-L\s[^\n]*)$", text, re.MULTILINE
    )
    return [c for c in commands if not re.search(r"-o\s*ControlPath=none\b", c)]


def run_stand_ssh(tmp_path: Path) -> str:
    """Прогнать скрипт action с подложным HOME и закреплённым ключом хоста; вернуть ~/.ssh/config."""
    workspace = tmp_path / "workspace"
    (workspace / ".github" / "ssh").mkdir(parents=True)
    key = tmp_path / "host_key"
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)], check=True)
    host_key = key.with_suffix(".pub").read_text().split()[:2]
    (workspace / ".github" / "ssh" / "known_hosts").write_text(f"{HOST} {' '.join(host_key)}\n")

    home = tmp_path / "home"
    home.mkdir()
    subprocess.run(
        ["bash", "-c", stand_ssh_script()],
        check=True,
        env={
            "PATH": os.environ["PATH"],
            "HOME": str(home),
            "GITHUB_WORKSPACE": str(workspace),
            "CI_KEY": "ключ",
            "SSH_USER": "deploy",
            "HOST": HOST,
        },
    )
    return (home / ".ssh" / "config").read_text()


def test_extractors_find_facts_that_are_present() -> None:
    """Шаблон, переставший находить факт, обязан падать здесь, а не молчать."""
    config = "Host stand\n  HostName 1.2.3.4\n  ControlMaster auto\n\nHost other\n  User x\n"
    assert host_options(config, "stand") == {"hostname": "1.2.3.4", "controlmaster": "auto"}
    assert expanded_socket_length("~/.ssh/cm-%C", "/home/runner") == len("/home/runner/.ssh/cm-") + 40 + 17
    assert tunnels_without_own_connection(
        '  ssh -o ExitOnForwardFailure=yes -f -N -L "9000:127.0.0.1:9000" stand\n'
        "      - run: ssh -f -N -L 1:127.0.0.1:1 stand\n"
    ) == [
        'ssh -o ExitOnForwardFailure=yes -f -N -L "9000:127.0.0.1:9000" stand',
        "ssh -f -N -L 1:127.0.0.1:1 stand",
    ]


def test_extractors_report_absence_instead_of_a_blank() -> None:
    assert host_options("Host other\n  User x\n", "stand") == {}
    assert tunnels_without_own_connection(
        "ssh -o ControlPath=none -o ExitOnForwardFailure=yes -f -N -L 1:127.0.0.1:1 stand\n"
    ) == []
    # Не туннель и не команда: обычный вызов и комментарий про -L.
    assert tunnels_without_own_connection('ssh stand "docker ps"\n# туннели (`ssh -f -N -L`)\n') == []


def test_stand_host_multiplexes_over_one_connection(tmp_path: Path) -> None:
    options = host_options(run_stand_ssh(tmp_path), "stand")
    assert options.get("hostname") == HOST, f"нет хоста stand с адресом сервера: {options}"
    assert options.get("controlmaster") == "auto", "у хоста stand нет ControlMaster auto"
    assert options.get("controlpersist"), "у хоста stand нет ControlPersist: master закрывался бы с первым вызовом"
    control_path = options.get("controlpath", "")
    assert "%C" in control_path, f"ControlPath без %C: {control_path!r}"
    length = expanded_socket_length(control_path, RUNNER_HOME)
    assert length <= SOCKET_PATH_LIMIT, (
        f"ControlPath {control_path!r} на раннере — {length} байт, предел сокета {SOCKET_PATH_LIMIT}"
    )


def test_tunnels_open_their_own_connection() -> None:
    """Туннель через master не ловит занятый порт и умирает с master."""
    files = sorted(p for p in GITHUB_DIR.rglob("*") if p.suffix in {".sh", ".yml", ".yaml"})
    offenders = {str(p): bad for p in files if (bad := tunnels_without_own_connection(p.read_text()))}
    assert not offenders, f"ssh -L без -o ControlPath=none: {offenders}"
