"""Песочница правил: `POST /api/v1/rules/parse` - разбор текста правил для редактора. Без БД и сети.

Проверка сессии, `Origin` и схемы тела - в `tests/test_problem_details.py`; здесь - что ручка
отвечает на допустимый запрос.
"""

from typing import Any

from fastapi.testclient import TestClient

from app.api.factory import create_app
from app.config import Settings

from .fakes.auth import sign_in

ORIGIN = "https://review.example"
PARSE_PATH = "/api/v1/rules/parse"

RULES = """\
# Правила ревью

## Error Handling Standards

- pattern: `app/**/*.py`
- exclude: `**/tests/**`

1. Отказ возвращается значением Result, а не исключением
3. Текст ошибки воспроизводим

## Naming

- pattern: `**/*.py`

## Docs

- pattern: docs/

1. Пример запуска рабочий
"""


def _client(**settings: Any) -> TestClient:
    app = create_app(
        Settings(
            database_url="postgresql+psycopg://test:test@localhost/test",
            rabbitmq_url="amqp://guest:guest@localhost//",
            github_webhook_secret="test-secret",
            public_origin=ORIGIN,
            **settings,
        )
    )
    sign_in(app)
    return TestClient(app, base_url=ORIGIN, headers={"Origin": ORIGIN})


def test_rules_are_parsed_with_their_lines_and_matching_paths() -> None:
    paths = ["app/billing.py", "app/tests/test_billing.py", "docs/run.md", "lib/x.py"]

    response = _client().post(PARSE_PATH, json={"rules": RULES, "paths": paths})

    assert response.status_code == 200
    assert response.json() == {
        "rules": [
            {
                "name": "Error Handling Standards",
                "line": 3,
                "patterns": ["app/**/*.py"],
                "excludes": ["**/tests/**"],
                # Номер пункта - позиция среди проверок, а не число, которое написал автор (`3.`).
                "checks": [
                    {"number": 1, "text": "Отказ возвращается значением Result, а не исключением", "line": 8},
                    {"number": 2, "text": "Текст ошибки воспроизводим", "line": 9},
                ],
                "matched_paths": ["app/billing.py"],
            },
            {
                "name": "Docs",
                "line": 15,
                "patterns": ["docs/"],
                "excludes": [],
                "checks": [{"number": 1, "text": "Пример запуска рабочий", "line": 19}],
                "matched_paths": ["docs/run.md"],
            },
        ],
        "rejected": [{"name": "Naming", "line": 11, "reason": "нет проверок"}],
    }


def test_without_paths_no_rule_matches_a_file() -> None:
    body = _client().post(PARSE_PATH, json={"rules": RULES}).json()

    assert [rule["matched_paths"] for rule in body["rules"]] == [[], []]


def test_empty_text_has_no_rules() -> None:
    """Пустой rules.md - не ошибка: ревью шло бы по стандартным критериям."""
    response = _client().post(PARSE_PATH, json={"rules": "", "paths": ["app/x.py"]})

    assert response.status_code == 200
    assert response.json() == {"rules": [], "rejected": []}


def test_text_over_the_review_limit_in_utf8_bytes_is_422() -> None:
    """Лимит - тот же, с которым ревью читает файл, и в байтах: кириллица весит вдвое."""
    client = _client(rules_max_bytes=10)

    fits = client.post(PARSE_PATH, json={"rules": "ыыыыы"})
    too_large = client.post(PARSE_PATH, json={"rules": "ыыыыыы"})

    assert fits.status_code == 200
    assert too_large.status_code == 422
    assert too_large.headers["content-type"] == "application/problem+json"
    problem = too_large.json()
    assert problem["code"] == "VALIDATION_ERROR"
    assert problem["errors"] == [{"where": "body.rules", "reason": "размер больше 10 байт в UTF-8"}]
    assert "ыыы" not in too_large.text
