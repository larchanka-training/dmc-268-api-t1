## 1. Рендер

- [x] 1.1 Чистая функция `render_startup(up_seconds, api_seconds)` в `scripts/build_summary.py` и подкоманда `startup`; тест в `tests/` рядом с тестами summary сверяет таблицу точной строкой

## 2. Замер в CI

- [x] 2.1 `.github/workflows/ci.yml`, job `compose-smoke`: метки времени вокруг `docker compose up -d --wait`, старт API по `docker inspect` (`State.StartedAt` → запись uvicorn «Application startup complete.» в логе с метками времени); проверка — в summary прогона на ветке видны обе цифры
- [x] 2.2 Шаг `python3 scripts/build_summary.py startup … >> "$GITHUB_STEP_SUMMARY"` только при успехе `up`; `actionlint`/`zizmor` зелёные

## 3. Документация

- [x] 3.1 `README.md`: базовая точка — размер образа (из summary job `image`) и холодный старт, медиана трёх прогонов `compose-smoke`, с датой и коммитом
- [x] 3.2 Change заархивирован (`openspec archive cold-start-metric`), `openspec validate --all --strict` зелёный
