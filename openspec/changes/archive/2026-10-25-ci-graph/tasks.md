# Tasks

## 1. Граф job'ов

- [x] 1.1 Удалить job `syntax`, убрать `needs` у job'ов `ci.yml`; проверить, что вне `tests/fixtures/` (вход парсера AST) все `.py` проходят через `ruff check`
- [x] 1.2 Убедиться, что gate в `deploy.yml` смотрит на `conclusion` всего прогона CI и граф на него не влияет; ссылки на `syntax` и compileall в документах обновить

## 2. python-env

- [x] 2.1 Composite action `.github/actions/python-env`: setup-uv того же SHA с uv 0.13.5 и Python 3.14, `uv sync --all-extras --frozen`
- [x] 2.2 Применить в lint и test `ci.yml`, `eval.yml`, `llm-live.yml`, `load.yml`; проверить, что `uv lock --check` после `uv sync --frozen` ловит устаревший uv.lock (sync его не трогает)

## 3. Пропуск теста — провал

- [x] 3.1 `tests/strict_skips.py` в `addopts`: код 1 при пропуске теста или модуля с `PYTEST_FAIL_ON_SKIP=1`, xfail и deselect не считаются, упавший прогон не перекрашивается
- [x] 3.2 Тесты плагина в подпроцессе: с переменной и без, пропуск модуля, xfail, упавший прогон
- [x] 3.3 grep и tee из четырёх workflow убрать, переменная — в env шага pytest; правило и локальный запуск — в `AGENTS.md`, `backend.md`, `README.md`

## 4. concurrency и деплой

- [x] 4.1 `queue` и `cancel-in-progress` выражениями от события; допустимость выражения в `queue` сверить с парсером actions/languageservices
- [x] 4.2 trivy по `<образ>:<sha>@<digest>` в job'е `image` `deploy.yml`; проверить, что trivy 0.70.0 берёт креды приватного реестра из `~/.docker/config.json` и сканирует ссылку tag@digest из реестра

## 5. Приёмка

- [x] 5.1 actionlint, zizmor, ruff, mypy, lint-imports, полный pytest с PostgreSQL 18 и RabbitMQ 4.4 — и он же с `PYTEST_FAIL_ON_SKIP=1`; openspec validate, проверка архива
