## Why

#386 (Sprint 5, DevOps) просит уменьшить образы и замерить холодный старт. Образы уже уменьшены (multi-stage, кэш uv в mount, без pip; summary job `image` показывает размер и разницу к `develop`). Холодный старт не меряет никто, поэтому нет базовой точки, и регресс старта после новой зависимости никто не заметит.

## What Changes

- Job `compose-smoke` (`.github/workflows/ci.yml`) замеряет на чистом раннере две длительности:
  - холодный `docker compose up -d --wait` — от команды до готовности всех сервисов, со сборкой и скачиванием образов;
  - старт API — от запуска контейнера `api` до первого `200` на `/health`.
- Обе цифры уходят в `$GITHUB_STEP_SUMMARY` job'а таблицей в том же стиле, что размер образа в job `image`.
- В `README.md` записывается базовая точка: размер образа и холодный старт на момент изменения.

## Capabilities

### New Capabilities

Нет.

### Modified Capabilities

Нет — `skip_specs: true`: меняется только CI, поведение сервиса прежнее.

## Impact

- `.github/workflows/ci.yml` (job `compose-smoke`), `scripts/build_summary.py` (новый подкомандный рендер раздела старта) или отдельный шаг, `README.md`.
- Прогон `compose-smoke` удлиняется на секунды: замер не добавляет работы, только время на `date`/`docker inspect`.
