"""Учёт реакции 👍/👎 на опубликованный комментарий бота.

Источник событий (опрос GitHub API / отдельный вебхук) подключается отдельно;
порт и инкремент в БД готовы под задачу #50 п.5.
"""

from app.application.ports.unit_of_work import UnitOfWork


def record_comment_feedback(
    uow: UnitOfWork, provider_comment_id: str, *, positive: bool
) -> bool:
    """Вернуть False, если комментарий с таким id провайдера не наш."""
    with uow:
        recorded = uow.published_comments.record_feedback(
            provider_comment_id, positive=positive
        )
        if recorded:
            uow.commit()
        return recorded
