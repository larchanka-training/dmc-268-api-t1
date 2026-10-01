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

# Пары (паттерн, замена): у большинства секретов замена — просто маркер,
# присваивание же оставляет имя переменной, чтобы контекст читался.
_SECRET_PATTERNS = (
    # AWS access key id, GitHub tokens, PEM private key headers.
    (re.compile(r"AKIA[0-9A-Z]{16}"), _REDACTED),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"), _REDACTED),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), _REDACTED),
    # Токены в нижнем регистре: эвристика строки требует смешанного регистра,
    # поэтому такие ключи ловятся только по узнаваемому префиксу.
    (re.compile(r"\bxox[a-z]-[A-Za-z0-9-]{10,}"), _REDACTED),
    (re.compile(r"\b[sr]k_live_[A-Za-z0-9]{16,}\b"), _REDACTED),
    (re.compile(r"\bAIza[A-Za-z0-9_-]{35}\b"), _REDACTED),
    # Секрет в присваивании: имя переменной намекает на секрет, значение —
    # длинная строка ключевого алфавита. Покрывает ключи без узнаваемого
    # префикса, вроде AWS secret access key.
    (
        re.compile(
            r"\b([A-Za-z0-9_-]*(?:secret|token|password|api[_-]?key)"
            r"[A-Za-z0-9_-]*)\s*[:=]\s*[\"']?[A-Za-z0-9_=/+.-]{20,}[\"']?",
            re.IGNORECASE,
        ),
        r"\1 = [REDACTED]",
    ),
)


def _looks_like_secret(text: str) -> bool:
    stripped = text.strip()
    if len(stripped) < 32:
        return False
    if re.fullmatch(r"[A-Za-z0-9_=/+.-]+", stripped) is None:
        return False
    # Требование классов символов отсекает не-секреты того же алфавита:
    # пути и dotted-имена — без цифр, SCREAMING_SNAKE-константы — без
    # нижнего регистра. Ключи вроде AWS secret access key смешивают регистр
    # и цифры.
    has_upper = any(c.isupper() for c in stripped)
    has_lower = any(c.islower() for c in stripped)
    has_digit = any(c.isdigit() for c in stripped)
    return has_upper and has_lower and has_digit


def redact(text: str) -> str:
    """Заменить распознанные секреты маркером; остальной код не трогать.

    Минимальный скучный набор: известные префиксы токенов плюс целые строки,
    которые выглядят как длинные значения ключей. Это тот же механизм, что
    потом будет вызываться при сохранении контекста прогона; расширять набор
    паттернов нужно здесь и только здесь.
    """
    result = text
    for pattern, replacement in _SECRET_PATTERNS:
        result = pattern.sub(replacement, result)
    lines = result.split("\n")
    redacted = []
    for line in lines:
        if _looks_like_secret(line):
            indent = line[: len(line) - len(line.lstrip())]
            redacted.append(f"{indent}{_REDACTED}")
        else:
            redacted.append(line)
    return "\n".join(redacted)


EMBEDDING_DIMENSION = 768
"""Размерность векторов профиля — одна на модель, схему и gateway.

Зафиксирована, а не настраивается: столбец `vector(N)` задаётся миграцией,
и настройка расходилась бы со схемой молча, отключая профиль через best-effort.
Смена размерности — это миграция схемы и перевложение всех чанков, отдельный
change, а не правка окружения.
"""


@dataclass(frozen=True, slots=True)
class ProfileLimits:
    """Лимиты профиля; значения приходят из конфигурации аргументом.

    Без дефолтов: единственное место значений — `Settings`, чтобы лимит
    нельзя было поменять в одном месте и не заметить в другом.
    """

    max_chunk_bytes: int
    retrieval_top_k: int
    retrieval_byte_budget: int


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

    Считается от нормализованного исходного текста окна, до редакции:
    дайджест фиксирует идентичность самого окна — повторный прогон того же
    кода даёт тот же дайджест, и вставка ложится идемпотентно.
    Идемпотентности по редакции ждать не стоит: текст с секретом и его
    отредактированная версия — разные тексты с разными дайджестами.
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def normalize_window_text(text: str) -> str:
    """Единая нормализация текста окна перед дайджестом, редакцией, вложением.

    NUL-байты вырезаются: PostgreSQL не принимает их в `text`, и один такой
    байт унёс бы весь батч вставки. Нормализацию обязаны вызывать оба пути —
    накопление и retrieval: иначе дайджест записи и дайджест запроса одного
    и того же окна расходятся, самосовпадение перестаёт отсекаться, а вектор
    запроса расходится с вектором записанного чанка.
    """
    return text.replace("\x00", "")


def build_chunks(
    windows: list[SurroundingWindow],
    repository_id: UUID,
    review_run_id: UUID,
    limits: ProfileLimits,
) -> list[ChunkDraft]:
    """Окна → черновики чанков, порядок входа сохраняется.

    Сверхлимитное окно пропускается целиком: чанк из половины окна врал бы
    про то, что ревьюер видел только его часть. Текст окна проходит
    `normalize_window_text` до дайджеста и редакции — единую для обоих
    путей, накопления и retrieval.
    """
    drafts: list[ChunkDraft] = []
    for window in windows:
        text = normalize_window_text(window.text)
        if len(text.encode("utf-8")) > limits.max_chunk_bytes:
            continue
        drafts.append(
            ChunkDraft(
                review_run_id=review_run_id,
                repository_id=repository_id,
                file_path=window.file_path,
                start_line=window.start_line,
                end_line=window.end_line,
                commit_sha=window.commit_sha,
                content_sha256=content_digest(text),
                body=redact(text),
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
