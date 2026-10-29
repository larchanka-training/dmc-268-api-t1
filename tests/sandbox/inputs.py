"""Что песочница ревьюит: PR из кейса корпуса, YAML-файла или диапазона git.

Это «сторона API» песочницы: вместо вебхука и записи в базу - готовый PR,
который `worker.seed_case` кладёт в фейковый GitHub и в фейковую базу.
"""

import logging
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

import yaml

from ..eval.rules.corpus import Case, load_case
from ..eval.scoring import ExpectedIssue

logger = logging.getLogger(__name__)

# Файлы крупнее этого в PR из git не берутся: дифф такого файла модели не нужен.
MAX_FILE_BYTES = 200_000
GIT_TIMEOUT_SECONDS = 30.0
# Режим gitlink в дереве git: запись сабмодуля, а не файл.
SUBMODULE_MODE = "160000"


class InputError(ValueError):
    """Вход песочницы не собрать: нет файла, не репозиторий, пустой дифф."""


@dataclass(frozen=True, slots=True)
class PrInput:
    """PR для песочницы: файлы обеих сторон и ожидаемые нарушения.

    `base_files` - файлы целевой ветки, которых касается PR (без
    `.review/rules.md`: его добавляет конфигурация правил); `head_files` -
    изменённые в PR файлы, `None` - файл удалён.
    """

    id: str
    title: str
    pr_title: str
    pr_description: str
    base_files: dict[str, str]
    head_files: dict[str, str | None]
    expected: tuple[ExpectedIssue, ...]


def from_case(case: Case) -> PrInput:
    """Кейс корпуса eval: все файлы новые, база пустая."""
    return PrInput(
        id=case.id,
        title=case.title,
        pr_title=case.pr_title,
        pr_description=case.pr_description,
        base_files={},
        head_files=dict(case.files),
        expected=case.expected,
    )


def load_case_file(path: Path) -> PrInput:
    """YAML в формате корпуса; раздел `expected` необязателен.

    Битый YAML и неверная структура (`files:` списком, нет ключа) - `InputError`,
    а не traceback: плохой вход - код выхода 2, а не 1.
    """
    if not path.is_file():
        raise InputError(f"файла кейса {path} нет")
    try:
        case = load_case(path, expected_required=False)
    except (yaml.YAMLError, AttributeError, KeyError, TypeError, ValueError) as error:
        raise InputError(f"кейс {path} не разобрать: {error}") from error
    return from_case(case)


def _git(repo: Path, *args: str) -> bytes:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            check=False,
            timeout=GIT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise InputError(f"git не запустить: {error}") from error
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        raise InputError(f"git {' '.join(args)}: {stderr or 'ошибка'}")
    return result.stdout


def _read_blob(repo: Path, rev: str, path: str) -> str | None:
    """Содержимое файла на ревизии; `None` - бинарный или слишком большой."""
    data = _git(repo, "show", f"{rev}:{path}")
    if len(data) > MAX_FILE_BYTES:
        logger.warning("%s: пропущен, %d байт больше %d", path, len(data), MAX_FILE_BYTES)
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        logger.warning("%s: пропущен, бинарный файл", path)
        return None


def _parse_range(range_spec: str) -> tuple[str, str]:
    base, sep, head = range_spec.partition("..")
    if not sep or not base or not head or head.startswith("."):
        raise InputError(f"диапазон «{range_spec}» не в форме BASE..HEAD")
    return base, head


def _sanitize(range_spec: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", range_spec).strip("_") or "git"


def from_git(range_spec: str, repo: Path) -> PrInput:
    """PR из диапазона `BASE..HEAD` локального репозитория.

    Дифф считается от merge-base, как у PR на GitHub (`BASE...HEAD`). База -
    файлы на merge-base, head - на `HEAD` диапазона. Бинарные и слишком
    большие файлы, а также сабмодули (gitlink) пропускаются с предупреждением.
    """
    base, head = _parse_range(range_spec)
    if _git(repo, "rev-parse", "--is-inside-work-tree").strip() != b"true":
        raise InputError(f"{repo} не рабочая копия git")
    for rev in (base, head):
        try:
            _git(repo, "rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}")
        except InputError as error:
            raise InputError(f"ревизии «{rev}» нет в {repo}") from error
    merge_base = _git(repo, "merge-base", base, head).decode().strip()
    # `--raw -z`: «:oldmode newmode oldsha newsha status\0path\0» - режимы
    # нужны, чтобы отличить gitlink (160000) от файла.
    raw = _git(repo, "diff", "--raw", "--no-renames", "-z", f"{base}...{head}")
    fields = raw.decode("utf-8", errors="surrogateescape").split("\0")

    base_files: dict[str, str] = {}
    head_files: dict[str, str | None] = {}
    for meta, path in zip(fields[0::2], fields[1::2], strict=False):
        if not meta or not path:
            continue
        old_mode, new_mode, *_, status = meta.lstrip(":").split()
        if SUBMODULE_MODE in (old_mode, new_mode):
            logger.warning("%s: пропущен, сабмодуль", path)
            continue
        kind = status[0]
        if kind != "A":
            old = _read_blob(repo, merge_base, path)
            if old is None:
                continue
            base_files[path] = old
        if kind == "D":
            head_files[path] = None
            continue
        new = _read_blob(repo, head, path)
        if new is None:
            base_files.pop(path, None)
            continue
        head_files[path] = new
    if not head_files:
        raise InputError(f"в диапазоне «{range_spec}» нет текстовых изменений")

    message = _git(repo, "log", "-1", "--format=%B", head)
    subject, _, body = message.decode("utf-8", errors="replace").strip().partition("\n")
    return PrInput(
        id=_sanitize(range_spec),
        title=f"git {range_spec}",
        pr_title=subject.strip(),
        pr_description=body.strip(),
        base_files=base_files,
        head_files=head_files,
        expected=(),
    )
