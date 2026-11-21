## Why

#386 (Sprint 5, DevOps) требует «сбор логов на случай сбоя во время презентации». Стек наблюдаемости (Loki, Tempo, Prometheus, Grafana) и алерты в Telegram уже работают, но сведения о них разбросаны по `infra/README.md`: «Логи контейнеров», «Алерты», «Деплой». За пять минут демо их не собрать. Доставку вебхуков GitHub App на публичный адрес после смены адреса тоже никто не проверял.

## What Changes

- Раздел «Демо: если стенд упал» в `infra/README.md` — одна страница действий по порядку:
  - где смотреть: алерт в Telegram, `GET /api/v1/status`, Grafana `:8443/grafana/`;
  - запросы Loki/Tempo по `review_run_id` и `trace_id` (из заголовка `X-Trace-ID`);
  - `docker logs` для каждого контейнера стека (`dmc268-*`);
  - перезапуск контейнеров `docker restart` (воркер — с `-t 30`); база и брокер перезапускаются тем же способом, а `docker rm`, тома и деплой — нет, по правилам сторожа деплоя;
  - проверка вебхуков: у доставки в Recent Deliveries в теле `id` прогона (`202` с `{"status":"ignored"}` — не успех), тестовый PR в демо-репозитории получает ревью;
  - план Б: стенд не поднялся за 10 минут — локальный compose с демо-данными (корневой `README.md`, «Демо-режим»).
- Чек-лист «за час до демо» в `docs/testing/RELEASE_SMOKE.md` — ссылкой на этот раздел.

## Capabilities

### New Capabilities

Нет.

### Modified Capabilities

Нет — `skip_specs: true`: только документация.

## Impact

- `infra/README.md`, `docs/testing/RELEASE_SMOKE.md` (SM-63).
- Зависит от `public-domain-origin`: адрес вебхука и Grafana в инструкции — доменные. До выбора домена раздел пишется с `<PUBLIC_ORIGIN>`, как в `docs/GITHUB_APP.md`.
