"""Профиль репозитория на ревью: чанки из окон окружения, отбор похожих.

Сущностей здесь минимум: `SurroundingWindow` приходит из сборки контекста,
`ChunkDraft` — то, что уходит на вложение и в профиль, `ProfileLimits` —
лимиты, которые слой приложения берёт из конфигурации. Все функции -
чистые: ни базы, ни сети, ни часов, ни случайности.
"""

import hashlib
import re
from dataclasses import dataclass
from uuid import UUID

_REDACTED = "[REDACTED]"

_SECRET_PATTERNS = (
    # AWS access key id, GitHub tokens, PEM private key headers.
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
)


def _looks_like_secret(text: str) -> bool:
    stripped = text.strip()
    return (
        len(stripped) >= 32
        and re.fullmatch(r"[A-Za-z0-9_=/+.-]+", stripped) is not None
    )


def redact(text: str) -> str:
    """Заменить распознанные секреты маркером; остальной код не трогать.

    Минимальный скучный набор: известные префиксы токенов плюс целые строки,
    которые выглядят как длинные значения ключей. Это тот же механизм, что
    потом будет вызываться при сохранении контекста прогона; расширять набор
    паттернов нужно здесь и только здесь.
    """
    result = text
    for pattern in _SECRET_PATTERNS:
        result = pattern.sub(_REDACTED, result)
    lines = result.split("\n")
    redacted = []
    for line in lines:
        if _looks_like_secret(line):
            indent = line[: len(line) - len(line.lstrip())]
            redacted.append(f"{indent}{_REDACTED}")
        else:
            redacted.append(line)
    return "\n".join(redacted)


@dataclass(frozen=True, slots=True)
class ProfileLimits:
    """Лимиты профиля; значения приходят из конфигурации аргументом."""

    max_chunk_bytes: int = 4096
    retrieval_top_k: int = 5
    retrieval_byte_budget: int = 16384


@dataclass(frozen=True, slots=True)
class SurroundingWindow:
    """Окно окружения: изменённый диапазон и текст файла в его границах.

    Путь и координаты — те же, что у `Hunk`; текст - содержимое файла
    в строках [start, end) окна.
    """

    file_path: str
    start_line: int
    end_line: int
    commit_sha: str
    text: str


@dataclass(frozen=True, slots=True)
class ChunkDraft:
    """Что отправляется на вложение и на запись в профиль.

    Текст уже отредактирован `redact`: дальше по конвейеру секретов не
    бывает, и хранить в памяти черновик с секретом нечему.
    """

    review_run_id: UUID
    repository_id: UUID
    file_path: str
    start_line: int
    end_line: int
    commit_sha: str
    content_sha256: str
    body: str


@dataclass(frozen=True, slots=True)
class EmbeddedChunk:
    """Черновик после вложения: готов к записи в профиль как есть."""

    draft: ChunkDraft
    embedding: tuple[float, ...]
    embedding_model: str


def content_digest(text: str) -> str:
    """Дайджест содержимого окна — ключ идемпотентности профиля.

    Считается от исходного текста окна до редакции: чанк без секретов и чанк
    с секретом, которые отличаются только редакцией, — один и тот же код.
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def build_chunks(
    windows: list[SurroundingWindow],
    repository_id: UUID,
    review_run_id: UUID,
    limits: ProfileLimits,
) -> list[ChunkDraft]:
    """Окна → черновики чанков, порядок входа сохраняется.

    Сверхлимитное окно пропускается целиком: чанк из половины окна врал бы
    про то, что ревьюер видел только его часть.
    """
    drafts: list[ChunkDraft] = []
    for window in windows:
        if len(window.text.encode("utf-8")) > limits.max_chunk_bytes:
            continue
        drafts.append(
            ChunkDraft(
                review_run_id=review_run_id,
                repository_id=repository_id,
                file_path=window.file_path,
                start_line=window.start_line,
                end_line=window.end_line,
                commit_sha=window.commit_sha,
                content_sha256=content_digest(window.text),
                body=redact(window.text),
            )
        )
    return drafts


@dataclass(frozen=True, slots=True)
class ScoredChunk:
    """Чанк, который вернул поиск, вместе с оценкой близости.

    Чем меньше `distance`, тем ближе код. `body` уже отредактирован:
    в профиль неотредактированный текст не попадает.
    """

    chunk_id: str
    file_path: str
    start_line: int
    end_line: int
    content_sha256: str
    body: str
    distance: float


@dataclass(frozen=True, slots=True)
class SimilarChunk:
    """Похожий чанк, попавший в уровень `similar` контекста."""

    file_path: str
    start_line: int
    end_line: int
    content_sha256: str
    body: str


def pick_similar(
    scored: list[ScoredChunk],
    current_digests: frozenset[str],
    limits: ProfileLimits,
) -> list[SimilarChunk]:
    """Отобрать чанки под лимиты: top-k, бюджет байтов, без самосовпадений.

    Вход уже упорядочен по возрастанию `distance` — адаптер сортирует то,
    что вернул поиск; домен лишь решает, что из этого проходит по лимитам.
    Дайджест, совпадающий с дайджестом текущего окна, отбрасывается: это
    тот же код, который ревью и так видит в диффе.
    """
    picked: list[SimilarChunk] = []
    total_bytes = 0
    for chunk in sorted(scored, key=lambda c: c.distance):
        if len(picked) >= limits.retrieval_top_k:
            break
        if chunk.content_sha256 in current_digests:
            continue
        if chunk.content_sha256 in {p.content_sha256 for p in picked}:
            continue
        size = len(chunk.body.encode("utf-8"))
        if total_bytes + size > limits.retrieval_byte_budget:
            continue
        picked.append(
            SimilarChunk(
                file_path=chunk.file_path,
                start_line=chunk.start_line,
                end_line=chunk.end_line,
                content_sha256=chunk.content_sha256,
                body=chunk.body,
            )
        )
        total_bytes += size
    return picked
