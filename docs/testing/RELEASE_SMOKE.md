# Smoke релизной сборки

Чеклист сквозной проверки стенда `develop` после деплоя (#385). Уровни тестирования — в
[TEST_PLAN](TEST_PLAN.md), качество модели — в [LLM_EVAL](LLM_EVAL.md) и
[`BENCHMARK_REPORT.md`](../../BENCHMARK_REPORT.md). Smoke не меряет качество ревью: он
проверяет, что собранная и выкаченная сборка работает целиком — вход, вебхуки, прогон,
публикация, правила, отказы и наблюдаемость.

## 1. Как проводить

**Стенд.** Сборка — вершина `develop`, выкаченная workflow `Deploy` (`.github/workflows/deploy.yml`,
устройство — [infra/README](../../infra/README.md), «Деплой»). Публичный вход — кабинет
https://62.169.24.237 (nginx интерфейса проксирует `/api/v1/…` с исходным путём), Grafana —
https://62.169.24.237:8443/grafana/. `/health` наружу не публикуется: проверка — на сервере
(`ssh stand`, хост из [infra/README](../../infra/README.md), «Деплой»).

**Демо-репозиторий.** Ручные шаги идут на демо-репозитории с установленной GitHub App
стенда (#387). Свой dev-App для локального прогона тех же шагов — [LOCAL_RUN](../LOCAL_RUN.md).
Код в PR демо-репозитория — синтетический, без секретов.

**Колонки.** `ID` — постоянный, на него ссылаются issue и отчёт. В «шаг» пометка
**[авто]** — шаг закрыт автотестом или job CI, в «результат» идёт ссылка на зелёный run этой
сборки; **[вручную]** — шаг выполняется руками на стенде. «Результат»: `пройден`,
`не пройден`, `не применим` (с причиной) и ссылка на run или PR демо-репозитория. «Баг» —
номер issue или `—`.

**Баги.** Каждое расхождение с «ожиданием» — issue: бэкенд — в этом репозитории, интерфейс —
в [`dmc-268-ui-t1`](https://github.com/larchanka-training/dmc-268-ui-t1/issues). В issue —
ID шага, коммит сборки, шаги воспроизведения, ожидание и факт, `X-Trace-ID` ответа или
`review_run_id` прогона (не тела запросов, не код из диффа, не токены). Критичный баг —
основной сценарий раздела не работает (вход невозможен, доставка не создаёт прогон, ревью не
публикуется или публикуется дважды, утечка данных или обход проверки `Origin` и подписи), —
**блокирует закрытие #385**; остальные заводятся и в отчёте перечисляются, но закрытие не держат.

**Итог** — в ручной раздел «Итоги smoke релизной сборки» файла
[`BENCHMARK_REPORT.md`](../../BENCHMARK_REPORT.md) (между маркерами `manual:begin`/`manual:end`,
регенерация отчёта его не трогает): коммит и run деплоя, дата, итог по разделам, список issue.
Заполненные таблицы — копия этого файла в описании PR или комментарии к #385.

## 2. Сборка и здоровье

| ID | Шаг | Ожидание | Результат | Баг |
|---|---|---|---|---|
| SM-01 | **[авто]** workflow `CI`, job `compose-smoke` на коммите сборки: `docker compose up --wait`, `curl /health`, `curl :8080/api/v1/status` через nginx интерфейса, воркер в `running` | job зелёный | | |
| SM-02 | **[авто]** workflow `Deploy` на коммите сборки | run зелёный; в summary — применённые миграции, шаг «Проверить, что воркеры работают» прошёл (`check-worker.sh`: все `worker_replicas` экземпляров запущены и 30 с не перезапускаются) | | |
| SM-03 | **[вручную]** `ssh stand 'docker inspect -f {{.State.Health.Status}} dmc268-api'` — healthcheck контейнера бьёт в `/health` | `healthy` | | |
| SM-04 | **[вручную]** `curl -si https://62.169.24.237/api/v1/status` | `200`, `status` и все три компонента `api`, `worker`, `queue` — `operational`; `unknown` — мониторинг не отвечает, это баг стенда | | |
| SM-05 | **[вручную]** `ssh stand 'docker ps --filter name=dmc268-worker --format "{{.Names}} {{.Status}}"'` | все экземпляры `Up`, без `Restarting` | | |
| SM-06 | **[вручную]** `uv run alembic heads` на коммите сборки; summary run деплоя, таблица миграций | голова одна; «Ревизия после» в summary равна ей; если деплой применил 0 миграций — та же строка в summary последнего деплоя с миграциями | | |

## 3. Авторизация

Поведение ручек — [TEST_PLAN](TEST_PLAN.md) §3.2.1, автотесты — `tests/test_auth_api.py`
(workflow `CI`, job `test`).

| ID | Шаг | Ожидание | Результат | Баг |
|---|---|---|---|---|
| SM-10 | **[вручную]** открыть кабинет, «Войти через GitHub» (`GET /api/v1/auth/github/login`), разрешить доступ | возврат в кабинет; cookie `session` с `HttpOnly; Secure; SameSite=Lax; Path=/` | | |
| SM-11 | **[вручную]** перезагрузить страницу; `GET /api/v1/auth/me` из DevTools | сессия жива, `200` с профилем | | |
| SM-12 | **[вручную]** выйти (`POST /api/v1/auth/logout`), затем `GET /api/v1/auth/me` с той же cookie из другой вкладки | `204`, затем `401 UNAUTHENTICATED` (Problem Details с `trace_id`) | | |
| SM-13 | **[вручную]** `curl -si -X POST https://62.169.24.237/api/v1/auth/logout -H 'Origin: https://evil.example'` | `403 ORIGIN_FORBIDDEN` — middleware режет запрос до сессии | | |
| SM-14 | **[авто]** `tests/test_auth_api.py`: `state` не совпал, отказ провайдера, внешний `redirect`, истёкшая cookie | job `test` зелёный | | |

## 4. Вебхуки

Сквозные сценарии — [TEST_PLAN](TEST_PLAN.md) §3.4; автотесты на стенде с фейковым GitHub —
`tests/e2e/test_rules_e2e.py` (workflow `CI`, job `test`).

| ID | Шаг | Ожидание | Результат | Баг |
|---|---|---|---|---|
| SM-20 | **[вручную]** открыть PR в демо-репозитории; в настройках GitHub App стенда — Advanced → Recent Deliveries | доставка `pull_request` с ответом `202`, тело — прогон (`ReviewJob`) | | |
| SM-21 | **[вручную]** `curl -si -X POST https://62.169.24.237/api/v1/webhooks/github -H 'X-GitHub-Event: pull_request' -H 'X-Hub-Signature-256: sha256=00' -d '{}'` | `401 WEBHOOK_SIGNATURE_INVALID`, прогона нет | | |
| SM-22 | **[вручную]** E2E-02: в Recent Deliveries нажать Redeliver у доставки из SM-20 | `202` с тем же `id` прогона; второго прогона и второго ревью в PR нет | | |
| SM-23 | **[авто]** E2E-02: `test_e2e_02_a_redelivered_webhook_creates_no_second_run_or_review`, `test_e2e_02b_…` | зелёные | | |
| SM-24 | **[вручную]** E2E-06: PR без дефектов (косметика) | check run «Идёт ревью» → «Ревью пройдено»; в PR только summary, inline — ноль | | |
| SM-25 | **[вручную]** E2E-01: PR с явным дефектом (SQL-инъекция конкатенацией) | прогон `completed`; inline-комментарий на строке дефекта и summary; при замечании не ниже порога gate — check run «Есть незакрытые замечания уровня … и выше» | | |
| SM-26 | **[авто]** E2E-01 и E2E-06 внутри RULES-02 и RULES-03 (`tests/e2e/test_rules_e2e.py`) | зелёные | | |

## 5. Инкрементальный анализ

Сейчас ревьюится дифф PR целиком на новом `head_sha`; повтор уже опубликованного замечания
отсекается по тредам GitHub (`tests/e2e/test_thread_dedup_e2e.py`,
`tests/e2e/test_incremental_review_e2e.py`, #222). Ревью только новых коммитов и отмена прогона
прежнего head новым push (E2E-03) — change [`incremental-review`](../../openspec/changes/incremental-review/), ещё не реализован (issue #425): приём на
`synchronize` прогоны не отменяет, отменяет только смена целевой ветки.

| ID | Шаг | Ожидание | Результат | Баг |
|---|---|---|---|---|
| SM-30 | **[вручную]** в PR из SM-25 запушить коммит, меняющий другой файл, дефект не трогать | новый прогон на новом `head_sha`; замечание к неизменённой строке в PR не повторяется; замечание к новому дефекту — отдельным inline | | |
| SM-31 | **[авто]** `tests/e2e/test_incremental_review_e2e.py`, `tests/e2e/test_thread_dedup_e2e.py` | зелёные | | |
| SM-32 | **[вручную]** E2E-03: push во время идущего прогона | **не применим** до change [`incremental-review`](../../openspec/changes/incremental-review/) (issue #425): сейчас прежний прогон доходит до конца, новый идёт параллельно; ожидание по коду — у нового head свой прогон, gate по последнему | | |
| SM-33 | **[вручную]** сменить целевую ветку PR, пока идёт прогон | прежний прогон `cancelled`, его находки не публикуются, для новой базы — новый прогон (`test_retarget_during_an_active_review_cancels_it_and_starts_a_new_one`) | | |

## 6. Песочница правил

Правила владельца — `.review/rules.md` в ветке по умолчанию ([RULES_GUIDE](../RULES_GUIDE.md));
статус файла отдаёт `GET /api/v1/repositories/{repository_id}/rules`, кабинет показывает его на
странице репозитория. Сценарии RULES-01…05 автоматизированы — [RULES_E2E_REPORT](RULES_E2E_REPORT.md) §2.

| ID | Шаг | Ожидание | Результат | Баг |
|---|---|---|---|---|
| SM-40 | **[вручную]** закоммитить в ветку по умолчанию корректный `.review/rules.md` (шаблон из `examples/rules/`), открыть правила репозитория в кабинете; статус смотреть в кабинете или `GET /api/v1/repositories/{repository_id}/rules` в DevTools — поля `status`, `rejected` | `status: loaded`, принятые правила с шаблонами путей и проверками, `rejected` пуст | | |
| SM-41 | **[вручную]** PR с нарушением пункта правил | inline-замечание со ссылкой на пункт правил | | |
| SM-42 | **[вручную]** пустой файл или только проза; статус смотреть в кабинете или `GET /api/v1/repositories/{repository_id}/rules` в DevTools — поля `status`, `rejected` | `status: empty`, причина в `rejected`; ревью идёт по стандартным критериям | | |
| SM-43 | **[вручную]** сломанный файл: раздел без `pattern`, раздел без проверок, повтор имени правила, незакрытый блок кода; отдельно — файл больше 32 КиБ (`RULES_MAX_BYTES`); статус смотреть в кабинете или `GET /api/v1/repositories/{repository_id}/rules` в DevTools — поля `status`, `rejected` | сломанные разделы — в `rejected` с причиной, остальные приняты; большой файл — `status: too_large`; прогон не падает | | |
| SM-44 | **[вручную]** удалить файл; статус смотреть в кабинете или `GET /api/v1/repositories/{repository_id}/rules` в DevTools — поля `status`, `rejected` | `status: absent` | | |
| SM-45 | **[авто]** RULES-01…05 (`tests/e2e/test_rules_e2e.py`), `tests/application/test_repository_rules.py` | зелёные | | |

## 7. Отказы

| ID | Шаг | Ожидание | Результат | Баг |
|---|---|---|---|---|
| SM-50 | **[авто]** E2E-05: `test_e2e_05_a_model_failure_fails_the_run_without_publishing_anything`; workflow `CI`, job `chaos` (`tests/chaos/test_llm_faults_chaos.py`, `test_provider_outage_chaos.py`) | зелёные: прогон `failed` с `failure_reason`, ревью в PR нет, check run «Ревью не удалось», сообщение в DLQ | | |
| SM-51 | **[вручную]** E2E-05 на стенде — только если модель недоступна сама (провайдер лёг, баланс исчерпан); выключать провайдера ради smoke не нужно | прогон `failed` с причиной, мусорного комментария в PR нет, check run «Ревью не удалось» | | |
| SM-52 | **[вручную]** E2E-08: PR, чей дифф в пределах `DIFF_MAX_BYTES`, но не влезает в пакет контекста модели (десятки файлов) | прогон `completed`; в summary блок «Файлы: …» с группами «Частично: часть изменений не вошла в лимит контекста» / «Не проверены: изменения не вошли в лимит контекста»; inline только по вошедшим файлам. Check run при неполном покрытии остаётся по замечаниям (`success` без них): `failure` gate за неполное покрытие — change [`review-limits`](../../openspec/changes/review-limits/), не баг этой сборки | | |
| SM-52a | **[авто]** покрытие в summary: `test_coverage_splits_files_by_what_the_model_saw`, `test_summary_lists_risks_and_files_under_details` (`tests/domain/test_review_summary.py`), `test_coverage_names_reviewed_and_filtered_files` (`tests/application/test_context_assembly.py`), `test_summary_lists_the_files_the_review_covered` (`tests/application/test_publish_review.py`) | зелёные | | |
| SM-53 | **[авто]** E2E-08, верхний предел: дифф больше `DIFF_MAX_BYTES` (5 МиБ) или `REVIEW_DIFF_MAX_LINES` — `test_a_diff_over_the_limit_fails_the_run_without_a_review` (`tests/application/test_review_pipeline.py`) | прогон `failed`, шаг `fetching_diff` с `DIFF_TOO_LARGE`, модель не вызывалась, check run «Ревью не удалось» | | |

## 8. Наблюдаемость

Устройство — [infra/README](../../infra/README.md), «Стек наблюдаемости» и «Алерты».

| ID | Шаг | Ожидание | Результат | Баг |
|---|---|---|---|---|
| SM-60 | **[вручную]** взять `X-Trace-ID` любого ответа API (SM-04, SM-12) и найти его в Grafana: Explore → источник Tempo → поиск по trace id | трейс есть, серверный спан запроса с тем же trace id; у прогона из SM-25 — спаны `review.<этап>` и `process review_jobs.qq` | | |
| SM-61 | **[вручную]** тот же `trace_id` в Loki: Explore → источник Loki, фильтр по полю `trace_id` | записи лога с этим `trace_id`, без тел запросов к модели, кода из диффа и токенов | | |
| SM-62 | **[вручную]** `ssh stand 'docker exec dmc268-alertmanager amtool alert query --alertmanager.url=http://127.0.0.1:9093'` после всех шагов | горящих алертов нет (кроме вызванных SM-51, если он был) | | |
