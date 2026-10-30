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
покрытия. Той же моделью проверяется конвейер логов: логи приходят в
коллектор по OTLP из SDK (путь через файлы Docker убран), а конвейер —
[otlp] → редакция → groupbyattrs → batch → Loki; derived field Loki →
Tempo остаётся. Валидация конфига самим коллектором — класс
`TestCollectorConfigValidation` ниже; поднятый стек делает ту же проверку
на каждом старте — с невалидным конфигом коллектор не запускается.
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


def test_browser_traces_go_only_to_their_own_pipeline() -> None:
    """Приёмник браузера — только в traces/browser, а ресурс ставит коллектор.

    Тело приходит из интернета через nginx интерфейса: в метрики и логи оно
    не попадает, а спан, подписанный как dmc-268-api, получает имя dmc-268-ui.
    """
    config = load_config()
    pipelines = config["service"]["pipelines"]
    users = [name for name, p in pipelines.items() if "otlp/browser" in p["receivers"]]
    assert users == ["traces/browser"]
    processors = pipelines["traces/browser"]["processors"]
    assert processors.index("transform/browser-resource") < processors.index("transform/redact")
    assert config["receivers"]["otlp/browser"]["protocols"] == {
        "http": {"endpoint": "0.0.0.0:4319", "max_request_body_size": 524288}
    }
    resource = config["processors"]["transform/browser-resource"]["trace_statements"][0]
    assert resource["context"] == "resource"
    assert resource["statements"] == [
        'keep_keys(attributes, ["telemetry.sdk.name", "telemetry.sdk.language", "telemetry.sdk.version", "service.version", "deployment.environment.name"])',
        'set(attributes["service.name"], "dmc-268-ui")',
    ]


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
# значения): ANSI-дубль сообщения uvicorn.
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

    Тело записи приходит из SDK — текст сообщения, отдельного атрибута
    message у OTLP-лога нет; отсутствие ключа delete_key пропускает молча
    (документация OTTL), поэтому записи без дубля не дают ошибок.
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


# --- конвейер логов: OTLP из SDK и порядок processors ------------------------------


def test_the_logs_pipeline_takes_otlp_and_redacts_before_grouping() -> None:
    """Конвейер логов: [otlp] → лимит → редакция → сервис → batch → Loki.

    Логи приходят по OTLP из SDK; редакция — только в logs: трейсы
    редактирует transform/redact, метрики не трогаются вовсе.
    """
    pipelines = load_config()["service"]["pipelines"]
    assert pipelines["logs"]["receivers"] == ["otlp"]
    assert pipelines["logs"]["processors"] == [
        "memory_limiter",
        "transform/redact-logs",
        "groupbyattrs/service",
        "batch",
    ]
    assert pipelines["logs"]["exporters"] == ["otlp_http/loki"]
    for processor in ("transform/redact-logs", "groupbyattrs/service"):
        assert processor not in pipelines["traces"]["processors"]
        assert processor not in pipelines["metrics"]["processors"]


def test_the_file_log_path_is_gone() -> None:
    """Путь через файлы Docker убран целиком: логи приходят по OTLP из SDK.

    Ни receiver file_log с его операторами stanza, ни фильтр чужих
    контейнеров (чужих логов в конвейере больше нет), ни смещения
    file_storage не должны остаться нигде в конфиге.
    """
    config = load_config()
    assert "file_log" not in config["receivers"]
    assert "filter/foreign-services" not in config["processors"]
    assert "file_storage" not in config["extensions"]
    assert "file_storage" not in config["service"]["extensions"]


# --- переход лог -> трейс: derived field Loki -------------------------------------


def test_loki_derived_field_takes_trace_id_from_structured_metadata() -> None:
    """Переход из лога в трейс — по лейблу trace_id, не по регэкспу тела.

    trace_id приходит в OTLP-логе от SDK и Loki держит его в structured
    metadata: регэксп по телу его не находил.
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

    Роль dmc268_monitor создаёт миграция 0013; пароль приходит из той же
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


# --- логи контейнеров: маскирование в коллекторе логов ------------------------------

# replace_pattern(body, "регэксп", "замена"): регэкспы — общее подмножество RE2
# и re (без обратных ссылок), поэтому re.sub даёт то же, что OTTL.
# Строки OTTL экранируют \\ и \" (в YAML они остаются как есть).
_REPLACE_BODY = re.compile(
    r'^replace_pattern\(body, "(?P<pattern>(?:[^"\\]|\\.)+)", "(?P<replacement>[^"$]+)"\)'
    r'(?: where (?P<where>attributes\["service.name"\] != "[^"]+"(?: and attributes\["service.name"\] != "[^"]+")*))?$'
)


def _unescape_ottl(text: str) -> str:
    return re.sub(r"\\(.)", r"\1", text)


def mask_container_log(line: str, service: str = "dmc268-ui") -> str:
    """Тело записи после правил replace_pattern из `otelcol-logs.yaml`, в порядке
    конфига; правило с where не применяется к перечисленным в нём сервисам."""
    config = yaml.safe_load(COLLECTOR_CONFIG.with_name("otelcol-logs.yaml").read_text(encoding="utf-8"))
    (block,) = config["processors"]["transform/container-logs"]["log_statements"]
    for statement in block["statements"]:
        match = _REPLACE_BODY.match(statement)
        # Правило в другой форме не должно тихо выпасть из эмуляции.
        assert match is not None or not statement.startswith("replace_pattern"), statement
        if match is not None and service not in re.findall(r'!= "([^"]+)"', match.group("where") or ""):
            line = re.sub(_unescape_ottl(match.group("pattern")), match.group("replacement"), line)
    return line


@pytest.mark.parametrize(
    ("line", "masked"),
    [
        (
            '203.0.113.7 - - [30/Oct/2026:12:00:00 +0000] "GET /api/v1/runs HTTP/1.1" 200 512 "-" "curl/8"',
            f'{REDACTED} - - [30/Oct/2026:12:00:00 +0000] "GET /api/v1/runs HTTP/1.1" 200 512 "-" "curl/8"',
        ),
        (
            "[error] 29#29: *1 connect() failed, client: 198.51.100.4, server: _",
            f"[error] 29#29: *1 connect() failed, client: {REDACTED}, server: _",
        ),
        (
            "[info] <0.1234.0> accepting AMQP connection 172.20.0.6:39654 -> 172.20.0.3:5672",
            f"[info] <0.1234.0> accepting AMQP connection {REDACTED}:39654 -> {REDACTED}:5672",
        ),
        (
            "UTC [77] STATEMENT:  ALTER ROLE dmc268_monitor password 'top''secret'",
            f"UTC [77] STATEMENT:  ALTER ROLE dmc268_monitor PASSWORD '{REDACTED}'",
        ),
        (
            "UTC [77] STATEMENT:  ALTER ROLE r PASSWORD E'a\\b'",
            f"UTC [77] STATEMENT:  ALTER ROLE r PASSWORD '{REDACTED}'",
        ),
        (
            "UTC [77] STATEMENT:  CREATE ROLE x PASSWORD 'ab?cd'",
            f"UTC [77] STATEMENT:  CREATE ROLE x PASSWORD '{REDACTED}'",
        ),
        (
            "UTC [77] STATEMENT:  ALTER ROLE r PASSWORD\t'x'",
            f"UTC [77] STATEMENT:  ALTER ROLE r PASSWORD '{REDACTED}'",
        ),
        (
            "UTC [77] DETAIL:  Key (installation_id)=(42) already exists.",
            f"UTC [77] DETAIL:  {REDACTED}",
        ),
        (
            "UTC [77] DETAIL:  Failing row contains (1, secret).",
            f"UTC [77] DETAIL:  {REDACTED}",
        ),
        (
            "UTC [77] DETAIL:  The slot \"standby\" is active for PID 5.",
            "UTC [77] DETAIL:  The slot \"standby\" is active for PID 5.",
        ),
        (
            "UTC [77] DETAIL:  Process 5 waits for ShareLock on transaction 9; blocked by process 6.",
            "UTC [77] DETAIL:  Process 5 waits for ShareLock on transaction 9; blocked by process 6.",
        ),
        (
            (
                '203.0.113.7 - - [30/Oct/2026:12:00:00 +0000] "GET /api/v1/auth/github/callback?code=X&state=Y HTTP/1.1" '
                '302 0 "https://github.com/login?return_to=%2Fx&client_id=Z" "curl/8"'
            ),
            (
                f'{REDACTED} - - [30/Oct/2026:12:00:00 +0000] "GET /api/v1/auth/github/callback?{REDACTED} HTTP/1.1" '
                f'302 0 "https://github.com/login?{REDACTED}" "curl/8"'
            ),
        ),
        (
            '[error] 29#29: *1 upstream timed out, client: 198.51.100.4, request: "GET /cb?code=X&state=Y HTTP/1.1", host: "h"',
            f'[error] 29#29: *1 upstream timed out, client: {REDACTED}, request: "GET /cb?{REDACTED} HTTP/1.1", host: "h"',
        ),
        (
            'UTC [77] FATAL:  password authentication failed for user "dmc268"; PostgreSQL 18.1, RabbitMQ 4.4.0',
            'UTC [77] FATAL:  password authentication failed for user "dmc268"; PostgreSQL 18.1, RabbitMQ 4.4.0',
        ),
    ],
)
def test_container_logs_hide_ip_addresses_and_secrets(line: str, masked: str) -> None:
    """IP клиента nginx (в начале access-лога и в error-логе), адреса соединений,
    пароль в тексте SQL и DETAIL postgres скрыты маркером; прочее — без изменений."""
    assert mask_container_log(line) == masked


@pytest.mark.parametrize("service", ["dmc268-postgres", "dmc268-postgres-replica"])
def test_container_logs_keep_the_jsonb_operator_in_postgres(service: str) -> None:
    """У postgres query не скрывается: в STATEMENT «?» — оператор JSONB."""
    line = "UTC [77] STATEMENT:  select data ? 'k' from t"
    assert mask_container_log(line, service) == line


def test_container_logs_hide_query_of_any_other_service() -> None:
    """Query скрыт и у сервиса с незнакомым тегом: правило не завязано на имя nginx."""
    line = "GET /cb?code=X&state=Y HTTP/1.1"
    assert mask_container_log(line, "dmc268-whatever") == f"GET /cb?{REDACTED} HTTP/1.1"


class TestCollectorConfigValidation:
    """Конфиг валидируется самим коллектором.

    Нужны Docker и подтянутый образ коллектора: `uv run pytest -m docker
    tests/infrastructure/test_collector_redaction.py`. Поднятый стек
    (профиль observability) проверяет то же самое на каждом старте —
    с невалидным конфигом коллектор не запускается.
    """

    @pytest.mark.docker
    @pytest.mark.parametrize(
        "config",
        [COLLECTOR_CONFIG, *(COLLECTOR_CONFIG.with_name(name) for name in ("otelcol-docker.yaml", "otelcol-logs.yaml"))],
    )
    def test_collector_accepts_the_config(self, config: Path) -> None:
        image = compose_collector_image()
        mount = f"{config}:/etc/otelcol-contrib/config.yaml:ro"
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
