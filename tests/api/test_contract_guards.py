"""Стражи единого формата ответов: время, этап сбоя, тела без обхода моделей.

Без базы: проверяются схема из кода, исходники `app/api` и модели.
"""

import re
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, get_args

import pytest
from pydantic import BaseModel, ValidationError

from app.api.factory import create_app
from app.api.reviews import ReviewRunStepResponse
from app.api.schemas import (
    UTC_DATETIME_PATTERN,
    FailedStage,
    ReviewErrorCode,
    UtcDatetime,
)
from app.config import Settings
from app.domain.enums import FAILURE_STAGES, ReviewRunStage, ReviewRunStepStatus
from app.domain.llm_errors import LlmError
from app.domain.run_steps import INTERNAL_ERROR
from app.domain.vcs_errors import VcsError
from app.infrastructure.vcs import (
    errors as vcs_errors,  # noqa: F401 - наследники VcsError
)

API_DIR = Path(__file__).resolve().parents[2] / "app" / "api"
SETTINGS = Settings(
    database_url="postgresql+psycopg://test:test@localhost/test",
    rabbitmq_url="amqp://guest:guest@localhost//",
    github_webhook_secret="test-secret",
    public_origin="https://review.example",
)
# Имя поля времени: `created_at`, `last_progress_at`.
TIME_FIELD = re.compile(r"_at$")


def _walk(node: Any, where: str = "") -> list[tuple[str, dict[str, Any]]]:
    """Все объекты схемы с путём до них."""
    found: list[tuple[str, dict[str, Any]]] = []
    if isinstance(node, dict):
        found.append((where, node))
        for key, value in node.items():
            found.extend(_walk(value, f"{where}/{key}"))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            found.extend(_walk(value, f"{where}/{index}"))
    return found


def test_every_date_time_in_the_schema_is_utc_datetime() -> None:
    """У каждого `format: date-time` — `pattern` формы `UtcDatetime`, и поле `*_at` — время.

    Поле `datetime` мимо `UtcDatetime` дало бы `date-time` без `pattern`, а время строкой —
    поле `*_at` без `date-time`. Параметры запроса — вход, у них `UtcDatetimeParam` без
    `pattern`: принимается любая зона.
    """
    schema = create_app(SETTINGS).openapi()
    # Ровно параметры операций: путь из `_walk` — `/paths/<путь со слешами>/<метод>/parameters/…`,
    # поэтому префиксы собираются из самой схемы, а не угадываются шаблоном.
    parameters = tuple(
        f"/paths/{path}/{method}/parameters/" for path, item in schema["paths"].items() for method in item
    )
    problems = []
    for where, node in _walk(schema):
        if where.startswith(parameters):
            continue
        if node.get("format") == "date-time" and node.get("pattern") != UTC_DATETIME_PATTERN:
            problems.append(f"{where}: date-time не через UtcDatetime")
        for name, prop in (node.get("properties") or {}).items():
            if TIME_FIELD.search(name) and prop.get("format") != "date-time":
                variants = prop.get("anyOf", [])
                if not any(v.get("format") == "date-time" for v in variants):
                    problems.append(f"{where}/properties/{name}: время без UtcDatetime")
    assert not problems, "\n".join(problems)


def test_routes_do_not_hand_build_json_bodies() -> None:
    """Ручка отдаёт модель ответа; `JSONResponse` собирает только построитель problem."""
    offenders = [
        path.name
        for path in sorted(API_DIR.glob("*.py"))
        if path.name != "errors.py" and "JSONResponse(" in path.read_text(encoding="utf-8")
    ]
    assert offenders == []


def test_failed_stage_is_the_domain_failure_stages() -> None:
    assert set(get_args(FailedStage)) == FAILURE_STAGES


def test_step_stage_and_status_are_the_domain_enums() -> None:
    """Литералы схемы шага - те же значения, что enum домена и тип в базе."""
    fields = ReviewRunStepResponse.model_fields
    assert list(get_args(fields["stage"].annotation)) == [s.value for s in ReviewRunStage]
    assert list(get_args(fields["status"].annotation)) == [s.value for s in ReviewRunStepStatus]


def _codes(base: type[LlmError | VcsError]) -> set[str]:
    classes: list[type[Any]] = [base]
    for cls in classes:
        classes.extend(cls.__subclasses__())
    return {cls.code for cls in classes}


def test_every_step_error_code_is_in_the_run_error_catalog() -> None:
    """Код шага - из каталога ошибок прогона (§8): иначе кабинет его не знает."""
    codes = _codes(LlmError) | _codes(VcsError) | {INTERNAL_ERROR}
    assert {"LLM_RATE_LIMITED", "PROMPT_TOO_LARGE", "PR_NOT_FOUND"} <= codes
    assert codes <= set(get_args(ReviewErrorCode))


class _Moment(BaseModel):
    at: UtcDatetime


def test_utc_datetime_is_written_in_utc_with_z() -> None:
    moscow = timezone(timedelta(hours=3))
    assert _Moment(at=datetime(2026, 10, 11, 13, 0, tzinfo=moscow)).model_dump(mode="json") == {
        "at": "2026-10-11T10:00:00Z"
    }
    assert _Moment(at=datetime(2026, 10, 11, 10, 0, 0, 120000, tzinfo=UTC)).model_dump(mode="json") == {
        "at": "2026-10-11T10:00:00.120000Z"
    }


def test_utc_datetime_rejects_time_without_zone() -> None:
    with pytest.raises(ValidationError):
        _Moment.model_validate({"at": "2026-10-11T10:00:00"})
