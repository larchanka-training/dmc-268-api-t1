"""Редакция чувствительных данных в коллекторе (change `add-observability-stack`).

Правила OTTL живут в единственном конфиге коллектора
`infra/observability/otelcol.yaml` — том же для локального стека и стенда.
Тест проверяет их на примерах из аудита #89, не поднимая стек: правила
интерпретируются тем их подмножеством, которое конфиг использует —
`set(..., "<скрыто>")` с условиями присутствия атрибута, непустого статуса
спана и имени события. Условие на статус вычисляется по фактической
семантике OTTL: строковый геттер `status.message` возвращает для Unset
пустую строку, а не nil (`pkg/ottl/compare.go`: string x nil — всегда
«not equal»), поэтому в конфиге — сравнение с `""`, а nil-сравнение
запрещено громким падением.
Правило вне этого подмножества упадёт на разборе и потребует обновить тест
вместе с собой; тихое выпадение атрибута из правил ловит проверка полноты
покрытия. Валидация конфига самим коллектором — класс
`TestCollectorConfigValidation` ниже; поднятый стек делает ту же проверку на
каждом старте — с невалидным конфигом коллектор не запускается.
"""

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
import yaml

from app.api.log_redaction import REDACTED

REPO = Path(__file__).resolve().parents[2]
COLLECTOR_CONFIG = REPO / "infra" / "observability" / "otelcol.yaml"
COMPOSE_FILE = REPO / "docker-compose.yml"

# Атрибуты и поля, которые обязаны накрываться правилами: url.* — спаны httpx
# и сервера, db.* — те, что пишут инструментирования SQLAlchemy и psycopg
# (новые semconv с opt-in `database` и старые на всякий случай), exception.* —
# событие исключения, status.message — описание статуса SQL-спанов.
REDACTED_TARGETS = (
    "url.query",
    "url.full",
    "db.system",
    "db.name",
    "db.statement",
    "db.operation",
    "db.user",
    "db.sql.table",
    "db.system.name",
    "db.namespace",
    "db.query.text",
    "db.operation.name",
    "status.message",
    "exception.message",
    "exception.stacktrace",
)

# Правило set(target, "значение") [where условия через or].
_RULE = re.compile(
    r'^set\((?P<target>attributes\["[^"]+"\]|status\.message), "(?P<value>[^"]+)"\)'
    r"( where (?P<where>.+))?$"
)
_TARGET = re.compile(r'^attributes\["([^"]+)"\]$')
_PRESENCE = re.compile(r'^attributes\["([^"]+)"\] != nil$')
# Сравнение строкового геттера статуса с литералом: status.message != "...".
_SET_STATUS_LITERAL = re.compile(r'^status\.message != "(?P<value>[^"]*)"$')

# Событие не имеет статуса: подменяет его в вычислении условий.
_EVENT_HAS_NO_STATUS = object()


def _runtime_status_message(status_message: Any) -> str:
    """Описание статуса, каким его видит OTTL-геттер в рантайме.

    Геттер `status.message` возвращает Go-строку: для спана с Unset-статусом
    это пустая строка, а не nil (`pkg/ottl/contexts/internal/ctxspan`), —
    поэтому `None` («статус не задан») в модели нормализуется к `""`.
    """
    if status_message is _EVENT_HAS_NO_STATUS:
        message = "условие status.message в контексте spanevent"
        raise ValueError(message)
    return status_message if status_message is not None else ""


def _split_where(where: str) -> tuple[tuple[str, ...], ...]:
    """Условия where в КНФ: конъюнкция групп-дизъюнкций.

    Скобки в подмножестве — только вокруг or-группы, как в
    `(A or B) and C`; and связывает сильнее or, поэтому разбор — сначала по
    and, потом по or. Другая расстановка скобок падает громко: вычислителя
    для неё в тесте нет.
    """
    for group in re.findall(r"\(([^()]*)\)", where):
        if " and " in group:
            message = f"скобки вокруг and вне подмножества теста: {where}"
            raise ValueError(message)
    without_parens = where.replace("(", " ").replace(")", " ")
    return tuple(
        tuple(part.strip() for part in conjunct.split(" or "))
        for conjunct in without_parens.split(" and ")
    )


@dataclass
class Rule:
    """Разобранное правило `set(...) where ...` из trace_statements.

    Условия — конъюнкция групп-дизъюнкций (`_split_where`): в группе
    достаточно одного выполненного, между группами — нужны все.
    """

    context: str
    target: str
    value: str
    conditions: tuple[tuple[str, ...], ...] = ()

    def applies(
        self, name: str | None, attributes: dict[str, Any], status_message: Any
    ) -> bool:
        return all(
            any(
                _condition_holds(condition, name, attributes, status_message)
                for condition in group
            )
            for group in self.conditions
        )


def _condition_holds(
    condition: str, name: str | None, attributes: dict[str, Any], status_message: Any
) -> bool:
    # Presence-check атрибутов — семантика атрибутов: нет атрибута, геттер
    # вернул nil. На строковый геттер статуса это НЕ переносится: тот всегда
    # возвращает строку.
    presence = _PRESENCE.fullmatch(condition)
    if presence is not None:
        return presence.group(1) in attributes
    literal = _SET_STATUS_LITERAL.fullmatch(condition)
    if literal is not None:
        # string x string — обычное сравнение по правилам OTTL
        # (`pkg/ottl/compare.go`): "" != "" — false, непустое описание — true.
        return _runtime_status_message(status_message) != literal.group("value")
    if condition == 'name == "exception"':
        return name == "exception"
    if "status.message" in condition:
        # В частности `status.message != nil`: в рантайме это no-op —
        # string x nil по таблице сравнения OTTL всегда «not equal», а nil
        # для строкового геттера вообще невозможен. Такое условие в конфиге
        # не защищает ничего; в модели оно вне подмножества, чтобы не
        # выглядело рабочим.
        message = (
            "сравнение статуса с nil — no-op в OTTL (string x nil всегда"
            " not equal); используй сравнение с пустой строкой:"
            f" {condition}"
        )
        raise ValueError(message)
    message = f"правило использует условие вне подмножества теста: {condition}"
    raise ValueError(message)


@dataclass
class Span:
    """Спан в терминах OTTL: атрибуты, описание статуса, события."""

    name: str
    attributes: dict[str, Any] = field(default_factory=dict)
    status_message: str | None = None
    events: list[dict[str, Any]] = field(default_factory=list)


def load_config() -> dict[str, Any]:
    config: dict[str, Any] = yaml.safe_load(COLLECTOR_CONFIG.read_text(encoding="utf-8"))
    return config


def parse_rules() -> list[Rule]:
    transform = load_config()["processors"]["transform/redact"]
    rules: list[Rule] = []
    for block in transform["trace_statements"]:
        context = block["context"]
        for statement in block["statements"]:
            match = _RULE.match(statement)
            if match is None:
                message = f"statement вне подмножества теста: {statement}"
                raise ValueError(message)
            target = match.group("target")
            attribute = _TARGET.fullmatch(target)
            target = attribute.group(1) if attribute else target
            where = match.group("where")
            conditions = _split_where(where) if where else ()
            rules.append(Rule(context, target, match.group("value"), conditions))
    return rules


def compose_collector_image() -> str:
    compose: dict[str, Any] = yaml.safe_load(COMPOSE_FILE.read_text(encoding="utf-8"))
    image: str = compose["services"]["collector"]["image"]
    return image


def apply_rules(rules: list[Rule], span: Span) -> Span:
    span_rules = [rule for rule in rules if rule.context == "span"]
    event_rules = [rule for rule in rules if rule.context == "spanevent"]
    for rule in span_rules:
        if rule.applies(span.name, span.attributes, span.status_message):
            _set(span, rule)
    for event in span.events:
        attributes = event["attributes"]
        for rule in event_rules:
            if rule.applies(event.get("name"), attributes, _EVENT_HAS_NO_STATUS):
                attributes[rule.target] = rule.value
    return span


def _set(span: Span, rule: Rule) -> None:
    if rule.target == "status.message":
        span.status_message = rule.value
        return
    span.attributes[rule.target] = rule.value


@pytest.fixture(scope="module")
def rules() -> list[Rule]:
    return parse_rules()


def test_all_sensitive_targets_are_covered(rules: list[Rule]) -> None:
    """Каждый чувствительный атрибут и поле накрыты правилом.

    Выпадение одного из них — тихая утечка: атрибут просто перестаёт
    редактироваться, поэтому покрытие проверяется отдельно от примеров.
    """
    covered = {rule.target for rule in rules}
    missing = set(REDACTED_TARGETS) - covered
    assert not missing, f"нет правил для: {sorted(missing)}"


def test_marker_is_the_same_as_in_the_app(rules: list[Rule]) -> None:
    """Маркер коллектора совпадает с маркером редакции в приложении."""
    assert {rule.value for rule in rules} == {REDACTED}


def test_redaction_sits_in_the_traces_pipeline() -> None:
    """Правила применяются к трейсам, а не к другим конвейерам."""
    pipelines = load_config()["service"]["pipelines"]
    assert "transform/redact" in pipelines["traces"]["processors"]
    assert "transform/redact" not in pipelines["metrics"]["processors"]
    assert "transform/redact" not in pipelines["logs"]["processors"]


def test_private_repository_url_is_hidden(rules: list[Rule]) -> None:
    """Аудит #89: url.full httpx-спана с приватным репозиторием скрыт."""
    span = apply_rules(
        rules,
        Span(
            name="GET",
            attributes={
                "http.request.method": "GET",
                "url.full": (
                    "https://api.github.com/repos/team/private-repo"
                    "/contents/app/main.py?ref=secret-branch"
                ),
                "url.query": "ref=secret-branch&token=github_pat_xxx",
            },
        ),
    )
    assert span.attributes["url.full"] == REDACTED
    assert span.attributes["url.query"] == REDACTED
    # Атрибуты остаются на месте: факт их наличия не теряется.
    assert span.attributes["http.request.method"] == "GET"


def test_database_values_and_status_description_are_hidden(rules: list[Rule]) -> None:
    """Аудит #89: значения db.* и DETAIL psycopg в описании статуса скрыты."""
    span = apply_rules(
        rules,
        Span(
            name="SELECT",
            attributes={
                "db.system.name": "postgresql",
                "db.namespace": "dmc268",
                "db.operation.name": "SELECT",
                "db.query.text": "SELECT * FROM users WHERE login = 'octocat'",
            },
            status_message="DETAIL: Key (login)=(octocat) already exists.",
        ),
    )
    assert span.attributes["db.query.text"] == REDACTED
    assert span.attributes["db.namespace"] == REDACTED
    assert span.attributes["db.system.name"] == REDACTED
    assert span.attributes["db.operation.name"] == REDACTED
    assert span.status_message == REDACTED
    # Атрибуты остаются на месте.
    assert set(span.attributes) == {
        "db.system.name",
        "db.namespace",
        "db.operation.name",
        "db.query.text",
    }


def test_successful_sql_span_status_stays_unset(rules: list[Rule]) -> None:
    """Успешный SQL-спан не получает бессмысленный маркер статуса.

    У него статус Unset: OTTL-геттер возвращает для него пустую строку (не
    nil), сравнение с `""` ложно — маркер не подставляется. Атрибуты при
    этом редактируются как у любого SQL-спана.
    """
    span = apply_rules(
        rules,
        Span(
            name="SELECT",
            attributes={
                "db.system.name": "postgresql",
                "db.query.text": "SELECT * FROM users WHERE login = 'octocat'",
            },
            status_message=None,
        ),
    )
    assert span.attributes["db.query.text"] == REDACTED
    assert span.status_message is None


def test_exception_event_message_and_stacktrace_hidden_type_kept(
    rules: list[Rule],
) -> None:
    """Аудит #89: текст ответа провайдера в событии exception скрыт, тип — нет."""
    span = apply_rules(
        rules,
        Span(
            name="process review_jobs",
            status_message="ValueError",
            events=[
                {
                    "name": "exception",
                    "attributes": {
                        "exception.type": "ValueError",
                        "exception.message": (
                            'HTTP 400: {"error":{"message":"текст ответа провайдера"}}'
                        ),
                        "exception.stacktrace": "Traceback (most recent call last):\n ...",
                    },
                }
            ],
        ),
    )
    (event,) = span.events
    assert event["attributes"]["exception.message"] == REDACTED
    assert event["attributes"]["exception.stacktrace"] == REDACTED
    # Тип исключения и статус по имени типа (после #122) сохраняются:
    # диагноз в трейсе остаётся.
    assert event["attributes"]["exception.type"] == "ValueError"
    assert span.status_message == "ValueError"


def test_spans_without_secrets_are_not_touched(rules: list[Rule]) -> None:
    """Правила не трогают спаны без чувствительных атрибутов."""
    server = apply_rules(
        rules,
        Span(name="GET /api/v1/reviews", attributes={"url.path": "/api/v1/reviews"}),
    )
    assert server.attributes["url.path"] == "/api/v1/reviews"
    assert server.status_message is None

    sweep = apply_rules(
        rules,
        Span(
            name="review.sweep",
            status_message="RuntimeError",
            events=[
                {
                    "name": "other",
                    "attributes": {"exception.type": "ValueError"},
                }
            ],
        ),
    )
    # Статус не-SQL-спана не редактируется: там имя типа исключения.
    assert sweep.status_message == "RuntimeError"
    # Событие с другим именем не редактируется.
    assert sweep.events[0]["attributes"] == {"exception.type": "ValueError"}


class TestCollectorConfigValidation:
    """Конфиг валидируется самим коллектором.

    Нужны Docker и подтянутый образ коллектора: `uv run pytest -m docker
    tests/infrastructure/test_collector_redaction.py`. Поднятый стек
    (профиль observability) проверяет то же самое на каждом старте —
    с невалидным конфигом коллектор не запускается.
    """

    @pytest.mark.docker
    def test_collector_accepts_the_config(self) -> None:
        image = compose_collector_image()
        mount = f"{COLLECTOR_CONFIG}:/etc/otelcol-contrib/config.yaml:ro"
        result = subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "-v",
                mount,
                image,
                "validate",
                "--config=/etc/otelcol-contrib/config.yaml",
            ],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        assert result.returncode == 0, (
            f"коллектор отклонил конфиг:\n{result.stdout}\n{result.stderr}"
        )
