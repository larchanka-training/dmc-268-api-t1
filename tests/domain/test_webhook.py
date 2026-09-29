"""Разбор payload'а вебхука GitHub: что запускает ревью, а что игнорируется.

Запущенные события сверяются с точным литералом `WebhookEvent`, поля взяты из
записанных payload'ов (`tests/fixtures/`) руками, а не пересчитаны той же
функцией. Игнорируемые события — это `None`, а не исключение: эндпоинт за них
отвечает 202, не 500.
"""

import json
from pathlib import Path
from typing import Any

import pytest

from app.domain.entities import WebhookEvent
from app.domain.webhook import extract_github_event

FIXTURES = Path(__file__).parents[1] / "fixtures"


def _load_payload(name: str) -> dict[str, Any]:
    payload: dict[str, Any] = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return payload


def test_opened_payload_returns_full_event() -> None:
    """`opened` — событие со всеми полями."""
    payload = _load_payload("github_webhook_opened.json")
    assert extract_github_event(payload) == WebhookEvent(
        action="opened",
        installation_id=512804923,
        repo_full_name="larchanka-training/dmc-268-api-t1",
        repo_provider_id="923478362",
        pr_number=6,
        head_sha="a1b2c3d4e5f6789012345678abcdef0123456789",
        source_branch="feat/webhook-intake",
        target_branch="develop",
        title="feat: приём вебхуков и VCS-шлюз",
        author="ilyassakhanov",
    )


def test_synchronize_payload_returns_event_with_new_head() -> None:
    """`synchronize` — тот же PR, head сдвинут на новый коммит."""
    payload = _load_payload("github_webhook_synchronize.json")
    event = extract_github_event(payload)
    assert event is not None
    assert event.head_sha == "0987654321abcdef0987654321abcdef09876543"
    assert event.pr_number == 6
    assert event.installation_id == 512804923
    assert event.source_branch == "feat/webhook-intake"
    assert event.target_branch == "develop"


@pytest.mark.parametrize(
    "action",
    [
        pytest.param("reopened", id="reopened: пользователь сам переназначает бота"),
        pytest.param("closed", id="closed"),
        pytest.param("assigned", id="прочее действие"),
        pytest.param("ready_for_review", id="прочее действие 2"),
    ],
)
def test_non_triggering_actions_are_ignored(action: str) -> None:
    """Всё, кроме `opened` и `synchronize`, — ignored: `None`, не исключение."""
    payload = _load_payload("github_webhook_opened.json")
    payload["action"] = action
    assert extract_github_event(payload) is None


def test_payload_without_installation_is_ignored() -> None:
    """Без installation токен для VCS не достать — событие игнорируется."""
    payload = _load_payload("github_webhook_opened.json")
    del payload["installation"]
    assert extract_github_event(payload) is None


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({}, id="пустой payload"),
        pytest.param({"action": "opened"}, id="нет ни PR, ни репозитория"),
        pytest.param(
            {
                "action": "opened",
                "installation": {"id": 1},
                "repository": {"id": 2, "full_name": "owner/repo"},
            },
            id="нет pull_request",
        ),
        pytest.param(
            {
                "action": "opened",
                "installation": None,
                "repository": {"id": 2, "full_name": "owner/repo"},
                "pull_request": {},
            },
            id="installation не объект",
        ),
        pytest.param(
            {
                "action": "opened",
                "installation": {"id": 1},
                "repository": {"id": 2, "full_name": "owner/repo"},
                "pull_request": {"number": 6},
            },
            id="в pull_request нет head/base/user",
        ),
    ],
)
def test_garbage_payload_is_ignored_without_raising(payload: dict[str, Any]) -> None:
    """Мусорный payload — `None`, эндпоинт не должен падать на 500."""
    assert extract_github_event(payload) is None
