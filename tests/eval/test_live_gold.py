"""Живой бенчмарк золотого набора: pass1 и pass2 живой модели и тайминги прогонов.

Маркер `eval`: основной прогон его исключает, запускает job `gold` workflow
`.github/workflows/eval.yml` с ключом организации. Набор всегда полный:
раннер с `--responses` требует ответы на каждый кейс; дешёвый пробный прогон -
`EVAL_REPETITIONS=1`, а не поднабор кейсов. Запросы синтетические - датасет
лежит в репозитории, код проверяемых репозиториев наружу не уходит.

Тест измеряет, а не гейтит: пороги LLM_EVAL §6 считает раннер по артефакту
(`make benchmark-report`), здесь они не проверяются. Тест падает, только если
измерение неполное - прогон кейса не дошёл до `completed`; падает после записи
артефакта, оплаченные ответы не теряются. Раскладка - design D3 change
`add-live-gold-benchmark`.

Окружение: `EVAL_REPETITIONS` (по умолчанию 1), `EVAL_REPORT_DIR` (по умолчанию
`eval-live-gold/`), `EUROROUTER_*` - как у живых тестов.
"""

import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ..conftest import requires_broker, requires_db, requires_eurorouter
from ..e2e.stack import Stack
from .dataset import DATASET_DIR, load_dataset
from .live import eval_repetitions, live_gateway, run_gold, run_timeout, write_artifact
from .prompts_hash import prompts_sha256
from .runner import dataset_sha256

pytestmark = [pytest.mark.eval, requires_db, requires_broker, requires_eurorouter]


def test_gold_benchmark_on_the_live_model(stack: Stack) -> None:
    settings = stack.settings
    repetitions = eval_repetitions()
    cases = load_dataset()
    timeout = run_timeout(settings)

    runs = run_gold(stack, live_gateway(settings), cases, repetitions, timeout=timeout)

    meta = {
        "model": settings.eurorouter_model,
        "reasoning_effort": settings.eurorouter_reasoning_effort,
        "prompts_sha256": prompts_sha256(),
        "dataset_sha256": dataset_sha256(DATASET_DIR),
        "repetitions": repetitions,
        "cases": len(cases),
        "date": datetime.now(UTC).date().isoformat(),
        "github_run_id": os.environ.get("GITHUB_RUN_ID"),
        "github_server_url": os.environ.get("GITHUB_SERVER_URL"),
        "github_repository": os.environ.get("GITHUB_REPOSITORY"),
    }
    out = Path(os.environ.get("EVAL_REPORT_DIR") or "eval-live-gold")
    write_artifact(out, runs, meta)
    print(f"\nартефакт живого бенчмарка: {out}")

    failed = [(r.case_id, r.repetition, r.status, r.failure) for r in runs if r.status != "completed"]
    assert not failed, f"не дошли до completed {len(failed)} прогонов: измерение неполное: {failed}"
