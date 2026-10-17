"""Тексты промптов продукта из `prompts/review/`.

Модели уходит часть файла после первой строки `---`; выше - пояснение для
людей. Читается один раз при старте воркера. Нет файла, разделителя или
тела - воркер не стартует: молча отправить модели пустые инструкции хуже,
чем не подняться.

Порта нет: это чтение конфигурации при старте, второго источника текстов
не предвидится.
"""

from collections.abc import Sequence
from pathlib import Path

from app.domain.prompt import PromptTexts

# Корень репозитория в исходниках и `/app` в образе: `Dockerfile` копирует
# `prompts/` рядом с пакетом `app`.
DEFAULT_PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts" / "review"

# Части системного сообщения ревью в порядке склейки. Новая часть (например,
# few-shot примеры) - новый файл и строка здесь; сборщик запроса не меняется.
REVIEW_SYSTEM_PARTS: tuple[str, ...] = (
    "system.md",
    "comment-templates.md",
    "output-format.md",
    "few-shot.md",
)
VERIFIER_SYSTEM_PARTS: tuple[str, ...] = ("verifier.md",)

_SEPARATOR = "---"


class PromptFileError(RuntimeError):
    """Файл промпта отсутствует или не отделяет тело от пояснения."""


def load_prompt_texts(
    root: Path,
    *,
    review_parts: Sequence[str] = REVIEW_SYSTEM_PARTS,
    verifier_parts: Sequence[str] = VERIFIER_SYSTEM_PARTS,
) -> PromptTexts:
    return PromptTexts(
        review_system=_join(root, review_parts),
        verifier_system=_join(root, verifier_parts),
    )


def _join(root: Path, names: Sequence[str]) -> str:
    return "\n\n".join(_body(root / name) for name in names)


def _body(path: Path) -> str:
    try:
        lines = path.read_text(encoding="utf-8").split("\n")
    except FileNotFoundError as exc:
        raise PromptFileError(f"нет файла промпта {path.name} в {path.parent}") from exc
    try:
        start = next(i for i, line in enumerate(lines) if line.strip() == _SEPARATOR) + 1
    except StopIteration:
        raise PromptFileError(f"{path.name}: нет строки {_SEPARATOR!r} перед текстом для модели") from None
    body = "\n".join(lines[start:]).strip()
    if not body:
        raise PromptFileError(f"{path.name}: пустой текст после {_SEPARATOR!r}")
    return body
