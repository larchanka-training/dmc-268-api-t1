"""Обвязка eval правил на стенде с моделью-оракулом - в каждом прогоне CI.

Живая модель платная и недетерминированная, поэтому её прогон - вручную
(`test_rules_eval.py`). А то, что корпус размечен верно и стенд доносит
каждое размеченное нарушение до PR, проверяется здесь: оракул отвечает
разметкой, и метрики обязаны выйти идеальными.
"""

import json
import time

import pytest
from pydantic import BaseModel

from app.application.ports.llm_gateway import (
    LlmGateway,
    LlmRequest,
    LlmResponse,
    LlmTask,
)

from ...conftest import requires_broker, requires_db
from ...e2e.stack import Stack
from ..report import config_metrics, config_outputs
from .corpus import CONFIGS, Case, load_corpus
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
    # Оракул отвечает по схеме с первой попытки: повторов и невалидных ответов нет.
    for config in configs:
        (outputs,) = config_outputs(results, config)
        assert outputs.valid_rate == 1.0, config
        assert (outputs.after_retry, outputs.invalid, outputs.no_answer) == (0, 0, 0), config


class _StalledOnce:
    """Оракул, который на ревью одного кейса один раз отвечает дольше таймаута воркера.

    `stack.work` падает по таймауту, а прогон доходит до конца во время ожидания
    отмены консьюмера: сообщение подтверждено, в очереди не остаётся ничего, что
    забрал бы следующий кейс.
    """

    def __init__(self, oracle: OracleLlmGateway, case: Case, delay: float) -> None:
        self._oracle = oracle
        self._paths = [json.dumps(path) for path in case.files]
        self._delay = delay
        self._stalled = False

    def complete[T: BaseModel](self, request: LlmRequest[T]) -> LlmResponse[T]:
        code = request.messages[2].content
        if (
            not self._stalled
            and request.task is LlmTask.REVIEW
            and any(p in code for p in self._paths)
        ):
            self._stalled = True
            time.sleep(self._delay)
        return self._oracle.complete(request)


def test_a_failed_run_is_recorded_and_the_eval_goes_on(stack: Stack) -> None:
    stalled = CORPUS[0]
    timeout = 5.0
    model = _StalledOnce(OracleLlmGateway(CORPUS), stalled, delay=timeout + 1)

    results = run_corpus(stack, model, CORPUS, ["rules"], 1, timeout=timeout)

    assert [r.case for r in results] == [c.id for c in CORPUS]
    failed, *passed = results
    assert failed.status == "error"
    assert failed.score is None
    assert failed.failure is not None
    assert failed.failure.startswith("AssertionError: воркер не обработал")
    assert all(r.status == "completed" for r in passed)
    assert all(r.score is not None for r in passed)
    (metrics,) = config_metrics(passed, "rules")
    assert (metrics.precision, metrics.recall) == (1.0, 1.0)


def test_a_message_left_by_a_failed_run_is_purged_before_the_next_case(
    stack: Stack, monkeypatch: pytest.MonkeyPatch
) -> None:
    work = stack.work
    calls = 0

    def fails_once_without_consuming(llm: LlmGateway, messages: int, *, timeout: float) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise AssertionError(f"воркер не обработал {messages} сообщений за {timeout:g} с")
        work(llm, messages, timeout=timeout)

    monkeypatch.setattr(stack, "work", fails_once_without_consuming)

    results = run_corpus(stack, OracleLlmGateway(CORPUS), CORPUS[:2], ["rules"], 1, timeout=60)

    failed, following = results
    assert failed.status == "error"
    # Без очистки воркер следующего кейса забрал бы сообщение упавшего прогона.
    assert following.status == "completed", following.failure
    (metrics,) = config_metrics([following], "rules")
    assert (metrics.precision, metrics.recall) == (1.0, 1.0)
