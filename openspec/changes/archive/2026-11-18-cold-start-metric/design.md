## Context

`compose-smoke` уже делает `cp .env.example .env` → `docker compose up -d --wait --wait-timeout 300` → `curl /health`. Раннер одноразовый: кэша образов и слоёв нет, поэтому каждый прогон — холодный. Summary размера образа пишет `scripts/build_summary.py` (`collect`/`render`) в job `image` (change `ci-build-summary`); у каждого job'а свой `$GITHUB_STEP_SUMMARY`, все они показываются на странице прогона.

## Goals / Non-Goals

**Goals:** две цифры в summary каждого прогона и базовая точка в README.
**Non-Goals:** порог с падением CI (раннеры GitHub шумят на десятки процентов); замер на стенде; разница к `develop` (это потребовало бы хранить прошлые замеры — отдельная задача, если понадобится).

## Decisions

- **D1. Холодный `up` — обёртка `date +%s%N` (наносекунды, целое) вокруг существующего шага.** Отдельный прогон стека ради замера удвоил бы время job'а. Сборка `ui` из удалённого репозитория входит в цифру — именно это ждёт внешний разработчик.
- **D2. Старт API — по меткам Docker, а не по `curl` в цикле.** `docker inspect -f '{{.State.StartedAt}}' <api>` даёт момент запуска, а момент готовности — метка времени записи uvicorn «Application startup complete.» в `docker compose logs --timestamps api`: первая такая запись не раньше `StartedAt`. Обе метки ставит Docker, поэтому цифра не зависит от частоты опроса шага. Healthcheck у сервиса `api` в compose не задан, поэтому `.State.Health.Log` не годится. Запись о готовности uvicorn пишет по завершении lifespan, а сокет открывает сразу после — разница в миллисекунды, на цифру не влияет. Шаг summary идёт после проверок `/health`, а не сразу после `up`: без healthcheck `--wait` ждёт лишь «running», и локальный прогон показал, что запись о готовности появляется примерно через секунду после возврата `up`. Шаг выполняется и после упавшей проверки (`!cancelled() && env.UP_MS != ''`), поэтому контейнер ищется с `docker compose ps -a`: упавший api тоже нужен. Таблица пишется через `| tee -a "$GITHUB_STEP_SUMMARY"` — summary шага через API не отдаётся, а базовую линию читают из лога job; шаг с `continue-on-error: true`, как summary в job `image`: summary не роняет job.
- **D3. Рендер — функция в `scripts/build_summary.py`.** Это чистая `render_startup(up_seconds, api_seconds) -> str` и `api_startup_seconds(started_at, log_lines)` рядом с `human_bytes` и `cell`: формат таблиц уже там, и тест рядом с тестами summary. Новый скрипт на одну таблицу был бы проходным модулем.

## Risks / Trade-offs

- [Шум раннера] → цифры информационные, без порога; базовая точка в README — медиана трёх прогонов.
- [Падение develop интерфейса ломает замер] → то же, что сейчас ломает `compose-smoke`; замер пишется только при успехе шага `up`.
