"""Eval правил на живой модели: влияние `.review/rules.md` на Precision/Recall.

Маркер `eval`: основной прогон его исключает, запускает workflow
`.github/workflows/eval.yml` с ключом организации. Запросы синтетические -
корпус лежит в репозитории, код проверяемых репозиториев наружу не уходит.

Тест измеряет, а не гейтит: падает, только если измерение неполное (прогон
не дошёл до публикации). Отчёт - `rules-eval.json` и `rules-eval.md` в
`EVAL_REPORT_DIR`, Markdown дописывается в сводку job GitHub Actions.

Окружение: `EVAL_REPETITIONS` (по умолчанию 1), `EVAL_CONFIGS` (через
запятую, по умолчанию все), `EUROROUTER_*` - как у живых тестов.
"""

import json
import os
from pathlib import Path

import httpx
import pytest

from app.infrastructure.container import llm_http_timeout
from app.infrastructure.llm.openai_compatible import OpenAiCompatibleGateway

from ...conftest import requires_broker, requires_db, requires_eurorouter
from ...e2e.stack import Stack
from ..report import to_json, to_markdown
from .corpus import CONFIGS, load_corpus
from .runner import prompts_sha256, run_corpus

pytestmark = [pytest.mark.eval, requires_db, requires_broker, requires_eurorouter]


def test_rules_eval_on_the_live_model(stack: Stack, tmp_path: Path) -> None:
    settings = stack.settings
    model = OpenAiCompatibleGateway(
        httpx.Client(base_url=settings.eurorouter_base_url, timeout=llm_http_timeout(settings)),
        settings.eurorouter_model,
        settings.eurorouter_keys,
        name="Eurorouter",
        max_attempts=settings.llm_max_attempts,
    )
    configs = [c.strip() for c in (os.environ.get("EVAL_CONFIGS") or ",".join(CONFIGS)).split(",")]
    unknown = set(configs) - set(CONFIGS)
    assert not unknown, f"неизвестные конфигурации: {sorted(unknown)}"
    repetitions = int(os.environ.get("EVAL_REPETITIONS") or 1)
    cases = load_corpus()
    # Два вызова модели на прогон, каждый - до таймаута на каждую попытку, и запас на остальное.
    timeout = 2 * settings.llm_timeout_seconds * settings.llm_max_attempts + 60

    results = run_corpus(stack, model, cases, configs, repetitions, timeout=timeout)

    meta = {
        "model": settings.eurorouter_model,
        "prompts_sha256": prompts_sha256(),
        "repetitions": repetitions,
        "cases": len(cases),
    }
    out = Path(os.environ.get("EVAL_REPORT_DIR") or tmp_path)
    out.mkdir(parents=True, exist_ok=True)
    markdown = to_markdown(results, configs, meta)
    (out / "rules-eval.md").write_text(markdown, encoding="utf-8")
    (out / "rules-eval.json").write_text(
        json.dumps(to_json(results, configs, meta), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write(markdown)
    print(f"\nотчёт eval: {out}\n\n{markdown}")

    failed = [r for r in results if r.score is None]
    assert not failed, f"не дошли до публикации {len(failed)} прогонов: измерение неполное"
