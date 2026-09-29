"""Извлечение события вебхука GitHub из payload'а.

Запускают ревью только `opened` и `synchronize`; `reopened`, `closed` и любые
прочие действия игнорируются — пользователь переназначает бота ревьюером сам.
Функция чистая и робкая к мусорному payload'у: всё, что не похоже на событие
pull request, даёт `None`, чтобы эндпоинт отвечал «ignored», а не 500.
`installation_id` достаётся из каждого payload'а — мульти-репозиторий по
конструкции, без настройки инсталляции в конфигурации.
"""

from typing import Any

from app.domain.entities import WebhookEvent

_TRIGGERING_ACTIONS: frozenset[str] = frozenset({"opened", "synchronize"})


def extract_github_event(payload: dict[str, Any]) -> WebhookEvent | None:
    """Достать событие ревью из payload'а вебхука или дать `None` (ignored).

    Базовый коммит не извлекается: `base.sha` из payload'а может быть
    устаревшим, поэтому всегда берётся из свежих метаданных PR.
    """
    try:
        if payload["action"] not in _TRIGGERING_ACTIONS:
            return None
        pull_request = payload["pull_request"]
        head = pull_request["head"]
        base = pull_request["base"]
        return WebhookEvent(
            action=payload["action"],
            installation_id=payload["installation"]["id"],
            repo_full_name=payload["repository"]["full_name"],
            repo_provider_id=str(payload["repository"]["id"]),
            pr_number=pull_request["number"],
            head_sha=head["sha"],
            source_branch=head["ref"],
            target_branch=base["ref"],
            title=pull_request["title"],
            author=pull_request["user"]["login"],
        )
    except (KeyError, TypeError):
        return None
