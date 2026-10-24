"""Типы публикации ревью на хостинг — часть контракта `VcsGateway`."""

from dataclasses import dataclass

from app.domain.enums import DiffSide


@dataclass(frozen=True, slots=True)
class InlineCommentDraft:
    file_path: str
    side: DiffSide
    line: int
    body: str


@dataclass(frozen=True, slots=True)
class PublishedInlineComment:
    file_path: str
    side: DiffSide
    line: int
    provider_comment_id: str


@dataclass(frozen=True, slots=True)
class PullRequestReviewPublication:
    summary_provider_comment_id: str | None
    # Порядок совпадает с черновиками инлайн-комментариев; `None` — не сопоставлен.
    inline_comments: tuple[PublishedInlineComment | None, ...]


@dataclass(frozen=True, slots=True)
class MergeGateCheck:
    conclusion: str
    title: str
    summary: str


@dataclass(frozen=True, slots=True)
class ReviewThreadState:
    """Тред ревью на хостинге сейчас, по корневому комментарию."""

    resolved: bool
    # Строка, на которой тред стоит в текущем диффе PR; `None` - тред устарел
    # (outdated): его строку изменили, и к нынешнему коду он не привязан.
    line: int | None
