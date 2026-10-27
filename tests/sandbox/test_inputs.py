"""Сборка PR песочницы из диапазона git: `from_git` и `_parse_range`."""

import subprocess
from pathlib import Path

import pytest

from .inputs import MAX_FILE_BYTES, InputError, _parse_range, from_git


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


@pytest.mark.parametrize("range_spec", ["HEAD", "..HEAD", "HEAD..", "A...B"])
def test_parse_range_rejects_not_a_two_dot_range(range_spec: str) -> None:
    with pytest.raises(InputError, match="BASE..HEAD"):
        _parse_range(range_spec)


def test_from_git_rejects_range_without_changes(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-q")
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "база")

    with pytest.raises(InputError, match="нет текстовых изменений"):
        from_git("HEAD..HEAD", tmp_path)


def test_from_git_skips_binary_and_oversized_files(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "commit", "-q", "--allow-empty", "-m", "база")
    (tmp_path / "ok.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "blob.bin").write_bytes(b"\xff\xfe\x00\x81 not utf-8")
    (tmp_path / "big.txt").write_text("a" * (MAX_FILE_BYTES + 1), encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "feat: файлы")

    pr = from_git("HEAD~1..HEAD", tmp_path)

    assert pr.head_files == {"ok.py": "x = 1\n"}
    assert pr.base_files == {}


@pytest.mark.parametrize("local_commit", [True, False], ids=["local-sha", "foreign-sha"])
def test_from_git_skips_submodule(tmp_path: Path, local_commit: bool) -> None:
    """Gitlink - не файл: ни текст коммита, ни ошибка `git show` в PR не попадают."""
    _git(tmp_path, "init", "-q")
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "база")
    sha = _git(tmp_path, "rev-parse", "HEAD") if local_commit else "1" * 40
    (tmp_path / "a.py").write_text("x = 2\n", encoding="utf-8")
    _git(tmp_path, "add", "a.py")
    _git(tmp_path, "update-index", "--add", "--cacheinfo", f"160000,{sha},sub")
    _git(tmp_path, "commit", "-q", "-m", "feat: сабмодуль")

    pr = from_git("HEAD~1..HEAD", tmp_path)

    assert pr.base_files == {"a.py": "x = 1\n"}
    assert pr.head_files == {"a.py": "x = 2\n"}
