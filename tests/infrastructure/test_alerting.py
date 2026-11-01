"""Алерты #100 без стека: правила, доставка и их входы описывают одно.

Поведение правил («горит / молчит» на синтетике) проверяет promtool по
infra/observability/alerts.test.yml в job observability-configs. Здесь —
связи между файлами, которые promtool не видит: каждый сигнал из задачи
покрыт правилом, имена метрик в правилах совпадают с теми, что пишут
источники, секреты Telegram приходят файлами по одним путям в compose и
infra, пороги записаны в README.
"""

import re
from pathlib import Path
from typing import Any

import yaml

from tests.infra_text import read_infra_text

REPO = Path(__file__).resolve().parents[2]
OBSERVABILITY_DIR = REPO / "infra" / "observability"
ALERTS = OBSERVABILITY_DIR / "alerts.yml"
ALERTMANAGER = OBSERVABILITY_DIR / "alertmanager.yml"
PROMETHEUS = OBSERVABILITY_DIR / "prometheus.yml"
OTELCOL = OBSERVABILITY_DIR / "otelcol.yaml"
OTELCOL_DOCKER = OBSERVABILITY_DIR / "otelcol-docker.yaml"
BACKUP_SCRIPT = REPO / "infra" / "postgres" / "backup.sh"
COMPOSE = REPO / "docker-compose.yml"
README = REPO / "infra" / "README.md"

# Сигналы из задачи #100 (и комментария про DLQ) → алерты, которые их ловят.
SIGNALS = {
    "контейнер не запущен": "ContainerDown",
    "контейнер перезапускается": "ContainerRestarting",
    "сервис unhealthy": "ServiceProbeFailed",
    "всплеск 5xx у API": "Api5xxSpike",
    "прогоны падают в воркере": "ReviewRunsFailing",
    "DLQ не пуст": "ReviewDlqNotEmpty",
    "бюджет LLM": "LlmBudgetNearLimit",
    "переполнение диска": "DiskSpaceCritical",
    "рост томов": "DiskWillFillIn24h",
    "реплика не в streaming": "ReplicaNotStreaming",
    "нет свежей копии": "BackupStale",
    "срок сертификата": "TlsCertificateExpiringSoon",
}


def _rules() -> list[dict[str, Any]]:
    groups = yaml.safe_load(ALERTS.read_text(encoding="utf-8"))["groups"]
    return [rule for group in groups for rule in group["rules"]]


def _alerts() -> dict[str, dict[str, Any]]:
    return {rule["alert"]: rule for rule in _rules() if "alert" in rule}


def test_every_signal_has_a_rule() -> None:
    missing = {signal: name for signal, name in SIGNALS.items() if name not in _alerts()}
    assert not missing, f"сигналы без правила: {missing}"


def test_every_alert_has_severity_and_texts() -> None:
    """severity — маршрут в alertmanager.yml, summary и description — текст сообщения."""
    for name, rule in _alerts().items():
        assert rule["labels"]["severity"] in {"critical", "warning"}, name
        assert rule["annotations"]["summary"], name
        assert rule["annotations"]["description"], name


def test_alert_names_are_unique() -> None:
    names = [rule["alert"] for rule in _rules() if "alert" in rule]
    assert len(names) == len(set(names))


def test_container_down_watches_real_container_names() -> None:
    """Имена в ContainerDown — контейнеры стенда: опечатка дала бы вечный алерт."""
    watched = set(re.findall(r'container_name="([^"]+)"', _alerts()["ContainerDown"]["expr"]))
    infra_names = set(re.findall(r'^\s*name\s*=\s*"(dmc268-[\w-]+)"', read_infra_text(), re.MULTILINE))
    # dmc268-ui поднимает стек репозитория dmc-268-ui-t1 на том же сервере.
    assert watched - {"dmc268-ui"} <= infra_names, watched - infra_names
    assert {"dmc268-api", "dmc268-worker", "dmc268-postgres", "dmc268-rabbitmq"} <= watched


def test_container_metrics_needed_by_alerts_are_enabled() -> None:
    receiver = yaml.safe_load(OTELCOL_DOCKER.read_text(encoding="utf-8"))["receivers"]["docker_stats"]
    assert receiver["metrics"]["container.uptime"] == {"enabled": True}
    assert receiver["metrics"]["container.restarts"] == {"enabled": True}


def test_token_spend_comes_from_the_collector_pipeline() -> None:
    """Ряд, по которому считается бюджет, собирает sqlquery основного коллектора."""
    config = yaml.safe_load(OTELCOL.read_text(encoding="utf-8"))
    receiver = config["receivers"]["sqlquery/llm-tokens"]
    names = [m["metric_name"] for q in receiver["queries"] for m in q["metrics"]]
    assert names == ["review_llm_tokens_month"]
    assert "sqlquery/llm-tokens" in config["service"]["pipelines"]["metrics"]["receivers"]
    # Только колонки, на которые у роли мониторинга есть SELECT (ревизия 0016).
    sql = receiver["queries"][0]["sql"]
    assert set(re.findall(r"\b(tokens_used|created_at|\w+_id|id)\b", sql)) <= {"tokens_used", "created_at"}
    assert "review_llm_tokens_month" in _alerts()["LlmBudgetNearLimit"]["expr"]


def test_backup_mark_matches_the_rule() -> None:
    metric = "dmc268_backup_last_success_timestamp_seconds"
    assert metric in BACKUP_SCRIPT.read_text(encoding="utf-8")
    assert metric in _alerts()["BackupStale"]["expr"]


def test_telegram_secrets_are_files_at_the_same_paths_everywhere() -> None:
    receiver = yaml.safe_load(ALERTMANAGER.read_text(encoding="utf-8"))["receivers"][0]
    telegram = receiver["telegram_configs"][0]
    assert "bot_token" not in telegram and "chat_id" not in telegram
    paths = {telegram["bot_token_file"], telegram["chat_id_file"]}

    compose = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    targets = {c["target"] for c in compose["services"]["alertmanager"]["configs"]}
    assert targets == paths

    infra = read_infra_text()
    for path in paths:
        assert f'file    = "{path}"' in infra, f"infra не заливает {path}"


def test_prometheus_loads_the_rules_and_sends_to_alertmanager() -> None:
    config = yaml.safe_load(PROMETHEUS.read_text(encoding="utf-8"))
    assert config["rule_files"] == ["/etc/prometheus/alerts.yml"]
    targets = config["alerting"]["alertmanagers"][0]["static_configs"][0]["targets"]
    assert targets == ["alertmanager:9093"]

    volumes = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"]["prometheus"]["volumes"]
    assert "./infra/observability/alerts.yml:/etc/prometheus/alerts.yml:ro" in volumes
    assert 'file    = "/etc/prometheus/alerts.yml"' in read_infra_text()


def test_thresholds_are_documented() -> None:
    """DoD #100: пороги записаны в infra/README.md — каждый алерт там назван."""
    readme = README.read_text(encoding="utf-8")
    undocumented = [name for name in _alerts() if f"`{name}`" not in readme]
    assert not undocumented, f"алертов нет в infra/README.md: {undocumented}"
