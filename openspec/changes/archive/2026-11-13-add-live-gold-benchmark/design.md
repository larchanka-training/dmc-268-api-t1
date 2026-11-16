# Design: add-live-gold-benchmark

## Context

- Офлайн-eval (`tests/eval/runner.py`, `make eval-offline`) читает ответы из
  `tests/eval/dataset/*/responses/run-<n>.json` (`_responses(case_dir, case_id)`), а
  `_check_repetitions` требует одинаковый набор повторов у всех кейсов. `dataset_sha256(root)`
  хэширует всё дерево, включая фикстуры ответов.
- Сквозной стенд `tests/e2e/stack.py` гоняет настоящие API и воркер на настоящих PostgreSQL и
  RabbitMQ с фейком GitHub; `Stack.work(llm, 1, timeout=...)` принимает gateway напрямую.
- `FakeGitHub.open_pull(...)` строит дифф из полных файлов (`file_diff`), а у золотого кейса есть
  только `diff.patch`, и hunk'и часто начинаются не с первой строки (`@@ -8,3 +8,8 @@`).
  Восстановление файлов из hunk'ов сдвинет номера строк и сломает эталонные якоря.
- Порт `LlmGateway` — один метод `complete(request) -> LlmResponse[T]`; `request.task` —
  `LlmTask.REVIEW` или `LlmTask.VERIFY`, `response.content` — уже провалидированная модель
  (`ReviewOutput` у первого прохода). Сырой текст провайдера порт не отдаёт.
- Невалидный ответ до порта не доходит: адаптер повторяет вызов до `llm_max_attempts`, провал —
  `LlmOutputError` и прогон `failed`. Исходы попыток — шаги прогона, их разбирает
  `classify_calls(stack.steps(run_id))` (`tests/eval/scoring.py`).
- `ReviewRun.duration_seconds` — монотонная разность от захвата до `completed`; шаги
  `ReviewRunStep(stage, duration_seconds, tokens_used, error_code)` — `fetching_diff`,
  `building_context`, `analysing` (первый проход и отсев), `verifying` (второй проход),
  `publishing`; у прогона — `tokens_used`, `verification`, `rejected_findings`,
  `verifier_dropped_findings`.
- `eval.yml` job `rules` запускает `pytest -m eval tests/eval` — весь каталог, а метка `eval` на
  PR запускает его автоматически.
- `test_rules_eval.py` собирает `OpenAiCompatibleGateway(...)` вручную.

## Goals / Non-Goals

**Goals:** измерить живую модель на золотом наборе до и после второго прохода тем же раннером и
теми же порогами §6, что офлайн-eval; тайминги и токены прогона; воспроизводимый отчёт из
артефакта без сети.

**Non-Goals:** изменение кода `app/`; гейт по порогам на живой модели (пороги — измерение,
решение по ним — отдельным PR, §6 «не опускается без решения»); LLM-судья; полные файлы
изменённых (не добавленных) файлов кейса в датасете; ручной smoke и сам платный прогон — их
делает координатор с пользователем, это пункт DoD #385, а не change'а.

## Decisions

### D1. PR из готового диффа

`FakeGitHub.open_pull_from_diff(number, *, base_sha, head_sha, diff, head_files=None, **details)`
в `tests/fakes/github_api.py` (расширяем фейк, а не новая обёртка) регистрирует `PullRequest` с
текстом `diff.patch` как есть и кладёт файлы head только для добавленных файлов
(`--- /dev/null`, hunk `-0,0`), где содержимое известно целиком. Остальные — `fetch_file` → 404 →
`SkippedAst(reason="missing")`, контекст деградирует штатно. Номера строк эталона сохраняются;
`_commentable_lines(pull.diff)` фейка проверяет якоря публикации по тому же диффу.

В `**details` (поля `PullRequest` фейка) идут ключи `mr.json` кейса с явным переименованием:
`title`→`title`, `description`→`body`, `source_branch`→`head_ref`, `target_branch`→`base_ref`:
без них враждебные кейсы с injection в описании PR (ADV-08,
`adversarial/ADV-08-pr-description-instruction`) проходили бы тривиально — описание не дошло бы
до промпта.

**Альтернатива:** восстанавливать файлы из hunk'ов — отвергнута, сдвигает номера строк.

### D2. Два снимка

Декоратор `RecordingLlmGateway` (`tests/eval/live.py`) делегирует вызов и запоминает
`content`: при `REVIEW` — ответ первого прохода, при `VERIFY` — вердикты верификатора.

- **pass1** — `ReviewOutput` из `REVIEW`-ответа (до `screen_findings`), сериализованный
  `model_dump_json(by_alias=True)` — camelCase, как фикстуры.
- **pass2** — `ReviewOutput(schema_version="1", findings=<Finding из базы → FindingOutput>,
  summary=<summary pass1>)`: `anchor.side`/`new_line|old_line`, `category`, `severity`,
  `message`, `suggestion` (→ `SuggestionOutput(diff=...)`), `confidence`, `rule`. Сводка — из
  pass1: метрики по ней не считаются, а проверка injection в тексте сводки должна видеть сказанное
  моделью.

Оба файла проходят `ReviewOutput.model_validate_json` раннера.

### D3. Раскладка артефакта

Каталог `EVAL_REPORT_DIR` (по умолчанию `eval-live-gold/`):

- `pass1/<case_id>/run-<n>.json`, `pass2/<case_id>/run-<n>.json`;
- `verdicts/<case_id>/run-<n>.json` — вердикты верификатора, если он вызывался;
- `timings.json` — на прогон: `case_id`, `repetition`, `status`, `failure`, `duration_seconds`,
  шаги `{stage: seconds}`, `tokens_used`, `verification`, `rejected_findings`,
  `verifier_dropped_findings`, `outputs` из `classify_calls`;
- `meta.json` — модель, effort, `prompts_sha256`, `dataset_sha256`, повторы, дата,
  `GITHUB_RUN_ID` и `GITHUB_SERVER_URL`, если есть;
- `pass1-report/`, `pass2-report/` — `report.json`/`report.md` раннера.

### D4. Сбой прогона не роняет бенчмарк

Как в `run_corpus` rules-eval: кейс пишется в `timings.json` со статусом `error`/`failed` и
причиной, а в `pass1`/`pass2` кладётся заглушка `{"liveRunFailed": "<причина>"}`. Она не
проходит схему и честно входит в долю невалидных, а набор повторов у всех кейсов остаётся
одинаковым — загрузчик не отвергает артефакт. Очередь между кейсами чистится
`stack.purge_queues()`. Тест падает в конце, если был хоть один сбой, — после записи всех файлов.

### D5. Опция раннера `--responses DIR`

`tests/eval/runner.py::main` → `load_dataset(args.dataset, responses=args.responses)`; ответы
читаются из `DIR/<case_id>/run-<n>.json`, ошибки — те же `DatasetError`, `_check_repetitions`
без изменений. С опцией в `meta` добавляется `responses_sha256`; без опции вывод и
`report.json` байт-в-байт прежние (снимок офлайн-отчёта не меняется).

### D6. Генератор отчёта

`tests/eval/benchmark_report.py` — чистые функции над двумя `report.json`, `timings.json`,
`meta.json` и кейсами (`load_dataset` + `evaluate_response` для классификации находок в
примерах); файлы читает и пишет только `main`:
`python -m tests.eval.benchmark_report --artifact DIR --out BENCHMARK_REPORT.md`.

- Детерминизм: сортировка по `case_id` и повтору, округление до двух знаков, без `now()` — дата
  из `meta.json`.
- Графики — Mermaid `xychart-beta`, только `bar`/`line` с явным диапазоном оси:
  Precision/Recall/F1/Abstention pass1 и pass2; Recall/Precision по категориям pass2;
  гистограмма `duration_seconds` по корзинам, плюс таблица среднее/медиана/p95 и по шагам
  `analysing`/`verifying`.
- Примеры: до 5 находок pass1, которых нет в pass2 и которые `evaluate_response` счёл FP
  («отсеяно верно»), до 3 таких же TP («отсеяно ошибочно»), до 3 оставшихся TP. Цитата —
  `message` модели и якорь, без кода диффа сверх строки якоря.
- Ручные разделы (итоги smoke, выводы) — между маркерами `<!-- manual:begin -->` и
  `<!-- manual:end -->`; при регенерации переносятся из старого файла.

### D7. Workflow

`eval.yml`: input `suite` (`rules|gold|all`, по умолчанию `rules`). `pull_request: labeled`
(`eval`) запускает только `rules`, как сейчас, и job `rules` сужается до `tests/eval/rules` —
иначе метка начала бы гонять платный золотой набор. Job `gold` — только `workflow_dispatch`:
те же сервисы и секреты, `timeout-minutes: 180` (35 кейсов × 3 повтора × 2 вызова),
`PYTEST_FAIL_ON_SKIP=1`, `EVAL_REPETITIONS: ${{ inputs.repetitions }}`,
`EVAL_REPORT_DIR: eval-live-gold`; input `configs` к `gold` не относится (это конфигурации
правил владельца для job `rules`). Шаги: `pytest -m eval tests/eval/test_live_gold.py`, затем с `if: always()` —
`make benchmark-report EVAL_LIVE_DIR=eval-live-gold` (раннер по pass1/pass2 с `|| true`: пороги —
измерение, а не гейт), Markdown — в `GITHUB_STEP_SUMMARY`, артефакт `gold-eval-report`.

### D8. Makefile

`eval-live-gold` — pytest живого набора, нужен env как в `eval.yml`. `benchmark-report` —
офлайн: раннер по pass1 и pass2 и генератор по скачанному артефакту `EVAL_LIVE_DIR`; работает
без сети и денег, им координатор собирает отчёт из артефакта.

### Поправки к исходному плану

Сверка плана с кодом изменила несколько предпосылок:

- **Valid-output rate.** По pass1-файлам он всегда 1.0: невалидный ответ до порта не доходит.
  Настоящий показатель — из `classify_calls` по шагам (первая попытка, повтор, провал); он
  пишется в `timings.json` и в отчёт отдельной строкой.
- **Время ревью** берётся из `duration_seconds` прогона и шагов, а не из `created_at`: на стенде
  прогоны идут подряд, ожидание в очереди смысла не имеет.
- **FPR и FDR.** FP/(TP+FP) — это FDR = 1 − Precision, а не FPR. В отчёте оба определения явно:
  **FPR (по кейсам)** — доля валидных ответов на negative-кейсах хотя бы с одним замечанием =
  1 − Abstention, сбойные прогоны — в Valid-output; **FDR** = FP/(TP+FP) = 1 − Precision. Пороговым считается только Abstention
  (§6).
- **Сужение job `rules`** до `tests/eval/rules` (D7): без него новый тест с маркером `eval` попал
  бы в автоматический прогон по метке.
- **Хэш датасета.** Поведение `dataset_sha256` без опции не меняется; хэш внешних ответов —
  отдельное поле `responses_sha256` (D5).
- **Сборка живого gateway** выносится в `live_gateway(settings)` (`tests/eval/live.py`), rules-eval
  её переиспользует — один источник модели и effort.
- **Тест обвязки** живого раннера — на модели-оракуле из фикстур `responses/run-1.json`, с
  маркером `integration`, в обычном CI; живую модель он не вызывает.

## Risks / Trade-offs

- [Без полного файла AST-контекст у изменённых (не добавленных) файлов пуст — модель видит
  меньше, чем в проде] → ограничение пишется в отчёт; большинство hunk'ов набора — новые файлы.
- [Бюджет EUrouter: 35 × 3 × до 2 вызовов плюс повторы адаптера ≈ 210–300 вызовов] →
  `run_gold` всегда гоняет полный набор (контракт `--responses` требует каталог на каждый кейс);
  дешёвый пробный прогон — `repetitions=1`, а не поднабор кейсов; оценка в описании PR.
- [TC-12 (большой дифф) — усечение или `PromptTooLargeError`] → это измерение; сбой не роняет
  бенчмарк (D4) и виден в отчёте.
- [Таймаут job 180 мин] → таймаут на прогон как у rules-eval; при превышении — пробный прогон с
  `repetitions=1` и решение о полном.
- [Mermaid `xychart-beta` в превью GitHub не рендерит конструкцию] → только `bar`/`line`, оси с
  явным диапазоном, проверка в превью PR.
- [Метка `eval` запустит платный набор] → сужение job `rules`, `gold` только `workflow_dispatch`.

## Open Questions

- Достаточно ли `GITHUB_RUN_ID`/`GITHUB_SERVER_URL` в `meta.json` для ссылки на run в отчёте
  (предлагается — да).
- Нужен ли пробный живой прогон одним повтором до полного — решает координатор с пользователем.
