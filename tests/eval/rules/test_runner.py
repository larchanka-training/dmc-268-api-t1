"""Обвязка eval правил на стенде с моделью-оракулом - в каждом прогоне CI.

Живая модель платная и недетерминированная, поэтому её прогон - вручную
(`test_rules_eval.py`). А то, что корпус размечен верно и стенд доносит
каждое размеченное нарушение до PR, проверяется здесь: оракул отвечает
разметкой, и метрики обязаны выйти идеальными.
"""

import pytest

from ...conftest import requires_broker, requires_db
from ...e2e.stack import Stack
from ..report import config_metrics
from .corpus import CONFIGS, load_corpus
from .oracle import OracleLlmGateway
from .runner import run_corpus

pytestmark = [pytest.mark.integration, requires_db, requires_broker]

CORPUS = load_corpus()


def test_the_oracle_scores_perfectly_in_every_rules_config(stack: Stack) -> None:
    configs = list(CONFIGS)

    results = run_corpus(stack, OracleLlmGateway(CORPUS), CORPUS, configs, 1, timeout=60)

    assert all(r.status == "completed" for r in results)
    for config in configs:
        (metrics,) = config_metrics(results, config)
        assert (metrics.precision, metrics.recall) == (1.0, 1.0), config
    # Ссылка на пункт возможна, только когда правила показаны модели.
    attribution = {c: config_metrics(results, c)[0].attribution for c in configs}
    assert attribution == {"absent": 0.0, "rules": 1.0, "empty": 0.0, "adversarial": 1.0}
