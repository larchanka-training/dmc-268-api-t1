"""Редакция чувствительных данных в коллекторе (change `add-observability-stack`).

Правила OTTL живут в единственном конфиге коллектора
`infra/observability/otelcol.yaml` — том же для локального стека и стенда.
Тест проверяет их на примерах из аудита #89, не поднимая стек: правила
интерпретируются тем их подмножеством, которое конфиг использует —
`set(..., "<скрыто>")` с условиями присутствия атрибута, непустого статуса
спана и имени события (для трейсов — `trace_statements`, для логов —
`log_statements`). Условие на статус вычисляется по фактической
семантике OTTL: строковый геттер `status.message` возвращает для Unset
пустую строку, а не nil (`pkg/ottl/compare.go`: string x nil — всегда
«not equal»), поэтому в конфиге — сравнение с `""`, а nil-сравнение
запрещено громким падением.
Правило вне этого подмножества упадёт на разборе и потребует обновить тест
вместе с собой; тихое выпадение атрибута из правил ловит проверка полноты
покрытия. Той же моделью проверяется конвейер логов: операторы file_log
(тело из `message`, дубль и `color_message` удалены), фильтр чужих
контейнеров по `service.name` и derived field Loki → Tempo. Валидация
конфига самим коллектором — класс `TestCollectorConfigValidation` ниже;
поднятый стек делает ту же проверку на каждом старте — с невалидным
конфигом коллектор не запускается.
"""

import json
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
DATASOURCES_CONFIG = (
    REPO / "infra" / "observability" / "grafana" / "provisioning" / "datasources" / "datasources.yaml"
)

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

# Правило set(target, "значение") [where условия через or]: target — атрибут
# (трейсов и логов), статус спана или тело лог-записи (set(body, ...)).
_RULE = re.compile(
    r'^set\((?P<target>attributes\["[^"]+"\]|status\.message|body), "(?P<value>[^"]+)"\)'
    r"( where (?P<where>.+))?$"
)
# Правило delete_key(attributes, "ключ"): чистка атрибута без редакции значения
# (дубль color_message). Отсутствующий ключ — no-op по документации OTTL.
_DELETE_KEY = re.compile(r'^delete_key\(attributes, "(?P<key>[^"]+)"\)$')
_TARGET = re.compile(r'^attributes\["([^"]+)"\]$')
_PRESENCE = re.compile(r'^attributes\["([^"]+)"\] != nil$')
# Сравнение строкового геттера статуса с литералом: status.message != "...".
_SET_STATUS_LITERAL = re.compile(r'^status\.message != "(?P<value>[^"]*)"$')

# Условие дропа фильтра чужих сервисов: условие log_record — само правило
# дропа («запись остаётся, пока выполнено хоть одно равенство service.name»),
# поэтому условие отрицания — not (A == "x" or B == "y").
_DROP_FOREIGN = re.compile(r'^not \((?P<keep>.+)\)$')
_KEEP_PAIR = re.compile(
    r'(?P<path>resource\.attributes\["service\.name"\]|attributes\["service\.name"\]) == "(?P<value>[^"]+)"'
)


@dataclass
class _NoStatus:
    """Контекст без статуса: подменяет его в вычислении условий.

    У события и лог-записи статуса нет: сравнение со статусом в их правилах —
    громкая ошибка разбора, а не молчаливое «не совпало».
    """

    context: str


_EVENT_HAS_NO_STATUS = _NoStatus("spanevent")
_LOGS_HAVE_NO_STATUS = _NoStatus("log")


def _runtime_status_message(status_message: Any) -> str:
    """Описание статуса, каким его видит OTTL-геттер в рантайме.

    Геттер `status.message` возвращает Go-строку: для спана с Unset-статусом
    это пустая строка, а не nil (`pkg/ottl/contexts/internal/ctxspan`), —
    поэтому `None` («статус не задан») в модели нормализуется к `""`.
    """
    if isinstance(status_message, _NoStatus):
        message = f"условие status.message в контексте {status_message.context}"
        raise TypeError(message)
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
    """Разобранное правило из trace_statements/log_statements.

    Условия — конъюнкция групп-дизъюнкций (`_split_where`): в группе
    достаточно одного выполненного, между группами — нужны все. Операция
    `set` редактирует значение, `delete_key` — удаляет атрибут целиком
    (без условий и без значения).
    """

    context: str
    target: str
    value: str | None
    conditions: tuple[tuple[str, ...], ...] = ()
    operation: str = "set"

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


@dataclass
class LogEntry:
    """Лог-запись в терминах OTTL: тело и атрибуты записи."""

    body: Any = None
    attributes: dict[str, Any] = field(default_factory=dict)


def load_config() -> dict[str, Any]:
    config: dict[str, Any] = yaml.safe_load(COLLECTOR_CONFIG.read_text(encoding="utf-8"))
    return config


def parse_rules() -> list[Rule]:
    """Правила редакции трейсов: процессор `transform/redact`."""
    return _parse_statements(load_config()["processors"]["transform/redact"], "trace_statements")


def parse_log_rules() -> list[Rule]:
    """Правила редакции логов: процессор `transform/redact-logs`."""
    return _parse_statements(load_config()["processors"]["transform/redact-logs"], "log_statements")


def _parse_statements(transform: dict[str, Any], key: str) -> list[Rule]:
    rules: list[Rule] = []
    for block in transform[key]:
        context = block["context"]
        for statement in block["statements"]:
            match = _RULE.match(statement)
            if match is not None:
                target = match.group("target")
                attribute = _TARGET.fullmatch(target)
                target = attribute.group(1) if attribute else target
                where = match.group("where")
                conditions = _split_where(where) if where else ()
                rules.append(Rule(context, target, match.group("value"), conditions))
                continue
            deletion = _DELETE_KEY.match(statement)
            if deletion is not None:
                rules.append(Rule(context, deletion.group("key"), None, operation="delete_key"))
                continue
            message = f"statement вне подмножества теста: {statement}"
            raise ValueError(message)
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


def apply_log_rules(rules: list[Rule], entry: LogEntry) -> LogEntry:
    """Применить правила логов к записи.

    Цели `set`-правил: `body` — тело, остальные — атрибуты. `delete_key`
    удаляет атрибут и молча пропускает отсутствие — как delete_key OTTL.
    """
    for rule in rules:
        if not rule.applies(None, entry.attributes, _LOGS_HAVE_NO_STATUS):
            continue
        if rule.operation == "delete_key":
            entry.attributes.pop(rule.target, None)
        elif rule.target == "body":
            entry.body = rule.value
        else:
            entry.attributes[rule.target] = rule.value
    return entry


# --- фильтр чужих контейнеров: service.name ---------------------------------------


def parse_foreign_filter() -> dict[str, frozenset[str]]:
    """Разобрать фильтр чужих сервисов: путь атрибута → допустимые имена.

    Условие читается как «not (путь == имя or ...)»: запись остаётся, пока
    выполнено хоть одно равенство. Другая форма условия или постороннее
    сравнение — громкое падение: вычислителя для них в тесте нет.
    """
    filter_config = load_config()["processors"]["filter/foreign-services"]
    statements = filter_config["logs"]["log_record"]
    allowed: dict[str, set[str]] = {}
    for statement in statements:
        match = _DROP_FOREIGN.match(statement)
        if match is None:
            message = f"условие фильтра вне подмножества теста: {statement}"
            raise ValueError(message)
        for pair in _KEEP_PAIR.finditer(match.group("keep")):
            allowed.setdefault(pair.group("path"), set()).add(pair.group("value"))
        remainder = _KEEP_PAIR.sub("", match.group("keep"))
        if remainder.replace("or", " ").strip():
            message = f"условие фильтра вне подмножества теста: {statement}"
            raise ValueError(message)
    return {path: frozenset(values) for path, values in allowed.items()}


def foreign_filter_keeps(
    allowed: dict[str, frozenset[str]], record_name: str | None, resource_name: str | None
) -> bool:
    """Пройдёт ли запись фильтр: имя сервиса в атрибутах записи или в ресурсе.

    Равенство с отсутствующим именем по правилам OTTL ложно (string x nil —
    «not equal»), поэтому `None` в вычислении просто не совпадает ни с чем.
    """
    record_values = allowed.get('attributes["service.name"]', frozenset())
    resource_values = allowed.get('resource.attributes["service.name"]', frozenset())
    return record_name in record_values or resource_name in resource_values


# --- конвейер file_log: операторы stanza ------------------------------------------


def _file_log_operators() -> list[dict[str, Any]]:
    return load_config()["receivers"]["file_log"]["operators"]


def _path_parts(entry: dict[str, Any], path: str) -> tuple[dict[str, Any], str]:
    """Контейнер и последний ключ stanza-пути (`attributes.message` → entry, ...)."""
    if '"' in path:
        message = f"путь с кавычками вне подмножества модели: {path}"
        raise ValueError(message)
    parts = path.split(".")
    container = entry
    for part in parts[:-1]:
        container = container[part]  # нет промежуточного поля — падение громкое
    return container, parts[-1]


def apply_file_log(envelope: str) -> dict[str, Any] | None:
    """Прогнать конверт Docker через операторы file_log, как их читает stanza.

    Модель покрывает используемые конфигом типы: `json_parser` (тело →
    атрибуты, `on_error: drop` на не-JSON), `move` и `remove` (на
    отсутствующем поле remove — no-op, запись не роняется). Порядок
    операторов — как в конфиге: перестановка `move` до `app-json` падает
    на KeyError. Разбор `time` конверта и блоков trace/severity на
    проверяемые поля не влияет и не моделируется.
    """
    entry: dict[str, Any] = {"body": envelope, "attributes": {}}
    for operator in _file_log_operators():
        kind = operator["type"]
        if kind == "json_parser":
            try:
                parsed = json.loads(entry["body"])
            except (TypeError, ValueError):
                if operator.get("on_error") == "drop":
                    return None
                raise
            entry["attributes"].update(parsed)
        elif kind == "move":
            source, source_key = _path_parts(entry, operator["from"])
            target, target_key = _path_parts(entry, operator["to"])
            target[target_key] = source.pop(source_key)
        else:
            # remove в модели нет сознательно: у него нет тихого пропуска
            # отсутствующего поля (on_error: send пишет ошибку на каждой
            # записи), поэтому конфиг его не использует — появившийся
            # оператор падает здесь громко.
            message = f"оператор вне подмножества модели: {kind}"
            raise ValueError(message)
    return entry


@pytest.fixture(scope="module")
def rules() -> list[Rule]:
    return parse_rules()


@pytest.fixture(scope="module")
def log_rules() -> list[Rule]:
    return parse_log_rules()


def test_all_sensitive_targets_are_covered(rules: list[Rule]) -> None:
    """Каждый чувствительный атрибут и поле накрыты правилом.

    Выпадение одного из них — тихая утечка: атрибут просто перестаёт
    редактироваться, поэтому покрытие проверяется отдельно от примеров.
    """
    covered = {rule.target for rule in rules}
    missing = set(REDACTED_TARGETS) - covered
    assert not missing, f"нет правил для: {sorted(missing)}"


def test_marker_is_the_same_as_in_the_app(
    rules: list[Rule], log_rules: list[Rule]
) -> None:
    """Маркер коллектора совпадает с маркером редакции в приложении."""
    setters = [rule for rule in rules + log_rules if rule.operation == "set"]
    assert {rule.value for rule in setters} == {REDACTED}


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


# --- редакция логов: transform/redact-logs ----------------------------------------

# Цели правил логов: стектрейс и сообщение исключения (semconv исключений —
# те же поля, что у событий exception в трейсах) и тело записи с исключением.
LOG_REDACTED_TARGETS = ("exception.stacktrace", "exception.message", "body")

# Атрибут, который redact-логи удаляют целиком (delete_key, без редакции
# значения): ANSI-дубль сообщения uvicorn. message не среди них — он уже
# переехал в тело оператором message-to-body.
LOG_DELETED_KEYS = ("color_message",)


def test_all_sensitive_log_targets_are_covered(log_rules: list[Rule]) -> None:
    """Каждая чувствительная цель лога накрыта set-правилом.

    Выпадение одной из них — тихая утечка: стектрейс ушёл бы в Loki
    открытым текстом, поэтому покрытие проверяется отдельно от примеров.
    """
    covered = {
        rule.target for rule in log_rules if rule.operation == "set"
    }
    missing = set(LOG_REDACTED_TARGETS) - covered
    assert not missing, f"нет правил для: {sorted(missing)}"


def test_redact_logs_deletes_only_the_color_duplicate(log_rules: list[Rule]) -> None:
    """delete_key удаляет ровно color_message: дубль ANSI uvicorn.

    message удалять нельзя — его значение уже в теле записи; отсутствие
    ключа delete_key пропускает молча (документация OTTL), поэтому записи
    без дубля не дают ошибок.
    """
    deleted = {
        rule.target for rule in log_rules if rule.operation == "delete_key"
    }
    assert deleted == set(LOG_DELETED_KEYS)
    assert "message" not in deleted

    entry = apply_log_rules(
        log_rules,
        LogEntry(
            body="Started server process",
            attributes={
                "service.name": "dmc-268-api",
                "severity": "INFO",
                "color_message": "\x1b[32mStarted server process\x1b[0m",
            },
        ),
    )
    assert "color_message" not in entry.attributes
    # Остальные атрибуты и тело не тронуты: delete_key — не редакция.
    assert entry.body == "Started server process"
    assert entry.attributes == {"service.name": "dmc-268-api", "severity": "INFO"}


def test_log_exception_fields_and_body_are_hidden(log_rules: list[Rule]) -> None:
    """Стектрейс и сообщение исключения скрыты; тело — у записи с исключением."""
    entry = apply_log_rules(
        log_rules,
        LogEntry(
            body="упало с SECRET-42",
            attributes={
                "service.name": "dmc-268-api",
                "severity": "ERROR",
                "exception.type": "LlmError",
                "exception.message": 'HTTP 400: {"error":{"message":"ответ провайдера"}}',
                "exception.stacktrace": "Traceback (most recent call last):\n ...",
            },
        ),
    )
    assert entry.attributes["exception.stacktrace"] == REDACTED
    assert entry.attributes["exception.message"] == REDACTED
    assert entry.body == REDACTED
    # Тип исключения сохраняется: диагноз остаётся.
    assert entry.attributes["exception.type"] == "LlmError"
    assert entry.attributes["service.name"] == "dmc-268-api"


def test_log_records_without_exceptions_keep_their_body(log_rules: list[Rule]) -> None:
    """Правила логов не трогают записи без исключения."""
    entry = apply_log_rules(
        log_rules,
        LogEntry(
            body="этап анализа завершён",
            attributes={"service.name": "dmc-268-api", "severity": "INFO"},
        ),
    )
    assert entry.body == "этап анализа завершён"
    assert entry.attributes == {"service.name": "dmc-268-api", "severity": "INFO"}


# --- конвейер метрик: ресурс в лейблы ------------------------------------------------


def test_the_prometheus_exporter_converts_the_resource_to_labels() -> None:
    """`job` у рядов = `service.name`: без конвертации ресурса все ряды выглядят
    как job="collector", а имя сервиса прячется в `exported_job` — ни запрос,
    ни алерт не различат сервисы."""
    exporter = load_config()["exporters"]["prometheus"]
    assert exporter["resource_to_telemetry_conversion"]["enabled"] is True


# --- конвейер логов: фильтр чужих контейнеров и порядок processors ----------------


def test_the_logs_pipeline_filters_foreign_services_then_redacts() -> None:
    """Порядок конвейера логов: лимит → фильтр → редакция → сервис → batch.

    Редакция и фильтр — только в logs: трейсы редактирует transform/redact,
    метрики не трогаются вовсе.
    """
    pipelines = load_config()["service"]["pipelines"]
    assert pipelines["logs"]["processors"] == [
        "memory_limiter",
        "filter/foreign-services",
        "transform/redact-logs",
        "groupbyattrs/service",
        "batch",
    ]
    for processor in ("filter/foreign-services", "transform/redact-logs"):
        assert processor not in pipelines["traces"]["processors"]
        assert processor not in pipelines["metrics"]["processors"]


def test_the_foreign_services_filter_lets_exactly_our_three_through() -> None:
    """Условие дропа покрывает оба места service.name и ровно три имени.

    У записей file_log имя — атрибут записи (до groupbyattrs/service),
    у OTLP-логов — ресурс.
    """
    ours = frozenset({"dmc-268-api", "dmc-268-worker", "dmc-268-migrate"})
    assert parse_foreign_filter() == {
        'attributes["service.name"]': ours,
        'resource.attributes["service.name"]': ours,
    }


def test_foreign_service_records_are_dropped_and_ours_pass() -> None:
    allowed = parse_foreign_filter()
    # Наши три имени проходят — и как атрибут записи, и как ресурс.
    for name in ("dmc-268-api", "dmc-268-worker", "dmc-268-migrate"):
        assert foreign_filter_keeps(allowed, name, None)
        assert foreign_filter_keeps(allowed, None, name)
    # Чужие контейнеры хоста и записи без имени нигде — дроп.
    assert not foreign_filter_keeps(allowed, "grafana", None)
    assert not foreign_filter_keeps(allowed, None, "postgres")
    assert not foreign_filter_keeps(allowed, "dmc-268-api-typo", "tempo")
    assert not foreign_filter_keeps(allowed, None, None)


# --- конвейер file_log: тело из message, дубль чистит redact-logs -----------------

# Запись приложения и конверт Docker вокруг неё — как их пишет json-file драйвер.
RECORD_TEXT = "упало с секретом SECRET-42"
RECORD_INNER = json.dumps(
    {
        "timestamp": "2026-10-24T10:00:00.000000Z",
        "severity": "ERROR",
        "message": RECORD_TEXT,
        "service.name": "dmc-268-api",
        "exception.stacktrace": "Traceback (most recent call last):\n ...",
    },
    ensure_ascii=False,
)
DOCKER_ENVELOPE = json.dumps({"log": RECORD_INNER, "time": "2026-10-24T10:00:00.123456789Z"})


def test_file_log_turns_message_into_body_and_keeps_no_duplicate() -> None:
    """Тело записи — текст сообщения; в Loki текст не хранится дважды."""
    entry = apply_file_log(DOCKER_ENVELOPE)
    assert entry is not None
    assert entry["body"] == RECORD_TEXT
    attributes = entry["attributes"]
    assert attributes["service.name"] == "dmc-268-api"
    assert attributes["severity"] == "ERROR"
    assert attributes["exception.stacktrace"] == "Traceback (most recent call last):\n ..."
    # message переехал в тело (move удаляет источник) — в атрибутах его нет.
    assert "message" not in attributes


def test_the_color_duplicate_is_gone_after_the_full_logs_pipeline(
    log_rules: list[Rule],
) -> None:
    """Полный конвейер логов не оставляет ни message, ни его ANSI-дубля.

    color_message удаляется не оператором remove (тот на каждой записи без
    дубля писал бы ошибку в лог коллектора), а delete_key в
    transform/redact-logs; здесь проверяется связка file_log → redact-logs.
    """
    envelope = json.dumps(
        {
            "log": json.dumps(
                {
                    "severity": "INFO",
                    "message": "Started server process",
                    "service.name": "dmc-268-api",
                    "color_message": "\x1b[32mStarted server process\x1b[0m",
                },
                ensure_ascii=False,
            ),
            "time": "2026-10-24T10:00:00.123456789Z",
        },
        ensure_ascii=False,
    )

    parsed = apply_file_log(envelope)
    assert parsed is not None
    # До redact-логов дубль ещё атрибут: file_log его не трогает.
    assert "color_message" in parsed["attributes"]

    entry = apply_log_rules(log_rules, LogEntry(parsed["body"], parsed["attributes"]))

    assert entry.body == "Started server process"
    # message переехал в тело, color_message удалён — в атрибутах их нет;
    # time конверта остаётся: stanza его не удаляет.
    assert entry.attributes == {
        "time": "2026-10-24T10:00:00.123456789Z",
        "severity": "INFO",
        "service.name": "dmc-268-api",
    }


def test_file_log_drops_records_that_are_not_app_json() -> None:
    """Не-JSON строки (логи самих контейнеров стека) в Loki не идут."""
    envelope = json.dumps({"log": "plain text line", "time": "2026-10-24T10:00:00Z"})
    assert apply_file_log(envelope) is None


# --- переход лог -> трейс: derived field Loki -------------------------------------


def test_loki_derived_field_takes_trace_id_from_structured_metadata() -> None:
    """Переход из лога в трейс — по лейблу trace_id, не по регэкспу тела.

    trace_id лежит в structured metadata OTLP-лога (блок trace: {} в
    json_parser коллектора): регэксп по телу его не находил.
    """
    datasources: dict[str, Any] = yaml.safe_load(DATASOURCES_CONFIG.read_text(encoding="utf-8"))
    loki = next(ds for ds in datasources["datasources"] if ds["type"] == "loki")
    field = next(f for f in loki["jsonData"]["derivedFields"] if f["datasourceUid"] == "tempo")
    assert field["matcherType"] == "label"
    assert field["matcherRegex"] == "trace_id"


# --- конвейер метрик: receiver postgresql ------------------------------------------


def test_the_postgresql_receiver_sits_in_the_metrics_pipeline_only() -> None:
    """Метрики базы идут через общий конвейер метрик (решение #89).

    Receiver подключён к metrics и только там: трейсы и логи к соединению
    с базой отношения не имеют. Отдельного экспортёра Postgres больше нет —
    на один контейнер меньше.
    """
    pipelines = load_config()["service"]["pipelines"]
    assert "postgresql" in pipelines["metrics"]["receivers"]
    assert "postgresql" not in pipelines["traces"]["receivers"]
    assert "postgresql" not in pipelines["logs"]["receivers"]


def test_the_postgresql_receiver_takes_connection_from_the_environment() -> None:
    """Реквизиты роли мониторинга — из окружения, литералов в конфиге нет.

    Роль dmc268_monitor создаёт миграция 0011; пароль приходит из той же
    переменной, что питает миграцию (локальный compose и infra различаются),
    поэтому в общем конфиге ему место только как ${env:...}. Endpoint —
    строго host:port, имя базы — отдельно (databases): суффикс /dbname
    endpoint этого receiver не принимает.
    """
    receiver = load_config()["receivers"]["postgresql"]
    assert receiver["username"] == "${env:POSTGRESQL_USERNAME}"
    assert receiver["password"] == "${env:POSTGRESQL_PASSWORD}"
    assert receiver["endpoint"] == "${env:POSTGRESQL_ENDPOINT}"
    assert receiver["databases"] == ["${env:POSTGRESQL_DATABASE}"]
    # Сбор чаще глобального 15s-интервала скрейпа Prometheus не нужен.
    assert receiver["collection_interval"] == "15s"


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
        # Реквизиты receiver postgresql конфиг берёт из окружения: при
        # валидации достаточно любых непустых значений — проверяется схема,
        # а не доступ к базе.
        postgresql_env = (
            "-e",
            "POSTGRESQL_USERNAME=otelu",
            "-e",
            "POSTGRESQL_PASSWORD=stub",
            "-e",
            "POSTGRESQL_ENDPOINT=localhost:5432",
            "-e",
            "POSTGRESQL_DATABASE=dmc268",
        )
        result = subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                *postgresql_env,
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
