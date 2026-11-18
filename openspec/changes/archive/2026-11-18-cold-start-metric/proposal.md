## Why

#386 (Sprint 5, DevOps) просит уменьшить образы и замерить холодный старт. Образы уже уменьшены (multi-stage, кэш uv в mount, без pip; summary job `image` показывает размер и разницу к `develop`). Холодный старт не меряет никто, поэтому нет базовой точки, и регресс старта после новой зависимости никто не заметит.

## What Changes

- Job `compose-smoke` (`.github/workflows/ci.yml`) замеряет на чистом раннере две длительности:
  - холодный `docker compose up -d --wait` — от команды до готовности всех сервисов, со сборкой и скачиванием образов;
  - старт API — от запуска контейнера `api` (`State.StartedAt`) до записи uvicorn «Application startup complete.» в его логе с метками времени (design, D2).
- Обе цифры уходят в `$GITHUB_STEP_SUMMARY` job'а таблицей в том же стиле, что размер образа в job `image`, через `| tee -a`: summary шага через API не отдаётся, и таблица нужна ещё и в логе job — по нему собирают базовую линию. Summary job `image` пишется так же.
- В `README.md` записывается базовая точка: размер образа и холодный старт на момент изменения.

## Capabilities

### New Capabilities

Нет.

### Modified Capabilities

Нет — `skip_specs: true`: меняется только CI, поведение сервиса прежнее.

## Impact

- `.github/workflows/ci.yml` (job `compose-smoke`), `scripts/build_summary.py` (подкоманда `startup` — рендер раздела старта), `README.md`.
- Прогон `compose-smoke` удлиняется на секунды: замер не добавляет работы, только время на `date`/`docker inspect`.
