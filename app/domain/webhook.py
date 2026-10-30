"""Извлечение события вебхука GitHub из payload'а.

Запускают ревью только `opened` и `synchronize`; `reopened`, `closed` и прочие
действия игнорируются — пользователь переназначает бота ревьюером сам. Свой ли
PR, решает `is_trusted_author` (правило — у `TRUSTED_AUTHOR_ASSOCIATIONS`).
Функция чистая и робкая к мусорному payload'у: всё, что не похоже на событие
pull request, даёт `None`, чтобы эндпоинт отвечал «ignored», а не 500.
`installation_id` достаётся из каждого payload'а — мульти-репозиторий по
конструкции, без настройки инсталляции в конфигурации.
"""

from typing import Any

from app.domain.entities import WebhookEvent

TRIGGERING_ACTIONS: frozenset[str] = frozenset({"opened", "synchronize"})
# shortcut: ревьюим только PR своих — ветка в самом репозитории (запушить её может только
# тот, у кого есть право записи) или автор-владелец, участник организации, коллаборатор
# (`author_association` считает GitHub, тело подписано). Одной связи мало: скрытое членство
# в организации GitHub пишет в вебхук как `CONTRIBUTOR` (#246). Без фильтра любой
# пользователь GitHub тратил бы ключ модели PR'ами из форка в публичный репозиторий.
# Временная защита до биллинга: после него — правило оплаты, а не запрет.
TRUSTED_AUTHOR_ASSOCIATIONS: frozenset[str] = frozenset({"OWNER", "MEMBER", "COLLABORATOR"})
# Действия `pull_request_review_thread`, которые сверяют закрытие треда.
THREAD_ACTIONS: frozenset[str] = frozenset({"resolved", "unresolved"})


def extract_github_event(payload: dict[str, Any]) -> WebhookEvent | None:
    """Достать событие ревью из payload'а вебхука или дать `None` (ignored).

    Базовый коммит не извлекается: `base.sha` из payload'а может быть
    устаревшим, поэтому всегда берётся из свежих метаданных PR.
    """
    try:
        if payload["action"] not in TRIGGERING_ACTIONS:
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
            author_association=pull_request.get("author_association"),
            # У head PR из удалённого форка `repo` пуст: такой PR считается чужим.
            from_fork=(head.get("repo") or {}).get("id") != payload["repository"]["id"],
        )
    except (KeyError, TypeError):
        return None


def is_trusted_author(event: WebhookEvent) -> bool:
    """PR свой: ревью тратит ключ модели только на него."""
    return not event.from_fork or event.author_association in TRUSTED_AUTHOR_ASSOCIATIONS
