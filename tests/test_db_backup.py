"""Копия базы вне сервера: db-backup.yml и .github/scripts/db-backup/.

Репозиторий публичный, артефакт скачает любой — поэтому главное здесь: без
ключа ничего не читается и не загружается, а загружается только шифр.
`pull.sh` гоняется настоящим bash и openssl с подложным `ssh`; `verify.sh`
с настоящей базой проверен локально (design.md change db-backup-off-server).
"""

import os
import re
import subprocess
from pathlib import Path

import yaml

SCRIPTS = Path(".github/scripts")
PULL = SCRIPTS / "db-backup/pull.sh"
VERIFY = SCRIPTS / "db-backup/verify.sh"
WORKFLOW = Path(".github/workflows/db-backup.yml")
KEY = "ключ-копий"
DUMP = b"PGDMP\x01\x10 not really a dump"

# Подложный ssh: вызовы — в журнал; find отдаёт FIND_OUT, cat — файл DUMP.
FAKE_SSH = """#!/usr/bin/env bash
echo "ssh $*" >> "$CALLS"
case "$*" in
  *" find "*) printf '%b' "$FIND_OUT" ;;
  *" cat "*) cat "$DUMP" ;;
  *) exit 1 ;;
esac
"""

OPENSSL = re.compile(r"openssl enc (?:-d )?(-aes-256-cbc -pbkdf2 -iter \d+)")


def pull(
    tmp_path: Path, *, key: str = KEY, find_out: str = ""
) -> tuple[subprocess.CompletedProcess[str], Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    (bin_dir / "ssh").write_text(FAKE_SSH)
    (bin_dir / "ssh").chmod(0o755)
    dump = tmp_path / "plain.dump"
    dump.write_bytes(DUMP)
    out = tmp_path / "out"
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "CALLS": str(tmp_path / "calls"),
        "DUMP": str(dump),
        "FIND_OUT": find_out,
        "STATE_BACKUP_KEY": key,
        "GITHUB_OUTPUT": str(tmp_path / "output"),
    }
    result = subprocess.run(
        ["bash", str(PULL), str(out)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    return result, out


def calls(tmp_path: Path) -> str:
    path = tmp_path / "calls"
    return path.read_text() if path.exists() else ""


def test_no_key_fails_before_reading_the_dump(tmp_path: Path) -> None:
    result, out = pull(tmp_path, key="", find_out="1 dmc268-20261110T200100Z.dump\\n")
    assert result.returncode == 1
    assert "::error::нет ключа STATE_BACKUP_KEY" in result.stdout
    assert calls(tmp_path) == ""
    assert not out.exists()


def test_newest_dump_is_encrypted_with_the_key(tmp_path: Path) -> None:
    find_out = "1731268861.5 dmc268-20261110T200100Z.dump\\n1731182461.5 dmc268-20261109T200100Z.dump\\n"
    result, out = pull(tmp_path, find_out=find_out)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "-mmin -1560" in calls(tmp_path)
    assert "cat /backups/dmc268-20261110T200100Z.dump" in calls(tmp_path)
    # На диске только шифр.
    assert [p.name for p in out.iterdir()] == ["dmc268-20261110T200100Z.dump.enc"]
    encrypted = out / "dmc268-20261110T200100Z.dump.enc"
    assert DUMP not in encrypted.read_bytes()
    plain = subprocess.run(
        [
            "openssl",
            "enc",
            "-d",
            "-aes-256-cbc",
            "-pbkdf2",
            "-iter",
            "600000",
            "-pass",
            "env:K",
            "-in",
            str(encrypted),
        ],
        env={**os.environ, "K": KEY},
        capture_output=True,
        check=True,
    ).stdout
    assert plain == DUMP
    assert (
        tmp_path / "output"
    ).read_text() == f"name=dmc268-20261110T200100Z\nfile={encrypted}\n"
    assert KEY not in result.stdout + result.stderr


def test_stale_dump_fails(tmp_path: Path) -> None:
    result, out = pull(tmp_path)
    assert result.returncode == 1
    assert "нет копии моложе 26 ч" in result.stdout
    assert " cat " not in calls(tmp_path)
    assert not out.exists()


def test_unexpected_name_is_not_passed_to_ssh(tmp_path: Path) -> None:
    result, _ = pull(tmp_path, find_out="1 x;reboot.dump\\n")
    assert result.returncode == 1
    assert " cat " not in calls(tmp_path)


def test_verify_without_key_fails(tmp_path: Path) -> None:
    env = {
        **os.environ,
        "STATE_BACKUP_KEY": "",
        "PG_CONTAINER": "c",
        "PGUSER": "u",
        "PGDATABASE": "d",
    }
    result = subprocess.run(
        ["bash", str(VERIFY), "x.dump.enc"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert "::error::нет ключа STATE_BACKUP_KEY" in result.stdout


def test_one_key_and_one_cipher_for_all_copies() -> None:
    """Копии базы и errored.tfstate открывает один ключ теми же параметрами."""
    for path in (PULL, VERIFY, SCRIPTS / "deploy/recover-state.sh"):
        params = OPENSSL.findall(path.read_text())
        assert params, path
        assert set(params) == {"-aes-256-cbc -pbkdf2 -iter 600000"}, path


def test_workflow_stores_only_encrypted_copy_for_90_days_and_checks_restore() -> None:
    text = WORKFLOW.read_text()
    jobs = yaml.safe_load(text)["jobs"]
    upload = next(
        s
        for s in jobs["backup"]["steps"]
        if str(s.get("uses", "")).startswith("actions/upload-artifact@")
    )
    assert upload["with"]["retention-days"] == 90
    assert upload["with"]["path"] == "${{ steps.pull.outputs.file }}"
    assert jobs["restore-check"]["needs"] == "backup"
    # Отметку для алерта OffsiteBackupStale — только проверенная копия.
    assert jobs["mark-success"]["needs"] == "restore-check"
    # Ключ — только через env шага, не в тексте команд и не в выводе.
    for job in jobs.values():
        for step in job["steps"]:
            assert "secrets." not in str(step.get("run", ""))
    assert text.count("secrets.STATE_BACKUP_KEY_DMC268_T1") == 2
