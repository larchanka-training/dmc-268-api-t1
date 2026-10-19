"""Ссылки на объекты у провайдера, которые сервис строит сам, без запроса к API хостинга."""

from app.domain.enums import Provider


def merge_request_url(provider: Provider, full_name: str, number: int) -> str | None:
    """Ссылка на страницу запроса на изменение у провайдера.

    `None` — для провайдера, у которого формат ссылки ещё не поддержан (GitLab появится
    вместе со своим адаптером): фронтенд тогда не показывает ссылку.
    """
    match provider:
        case Provider.GITHUB:
            return f"https://github.com/{full_name}/pull/{number}"
        case _:
            return None
