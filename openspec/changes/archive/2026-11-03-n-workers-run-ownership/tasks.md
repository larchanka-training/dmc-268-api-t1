# Tasks

## 1. Запись

- [x] 1.1 Тесты на настоящей базе: два захвата, очистка против прогресса, две очистки, два исхода шага
- [x] 1.2 `previous` в `ReviewRunRepo.update`, `RunStateConflict`; шаг закрывается поверх `running`
- [x] 1.3 `clock_timestamp()` в `updated_at`

## 2. Воркер

- [x] 2.1 Тесты: прогон в работе не тронут, проигранный захват, очистка пропускает изменившийся
- [x] 2.2 Захват в `run_review`, двухфазная очистка
- [x] 2.3 Тесты: живой воркер, чей прогон вымели, шаги выметенного прогона в API, `updated_at`

## 3. Проверка

- [x] 3.1 Chaos-сценарии смерти воркера под новое поведение
- [x] 3.2 `ruff`, `mypy`, `lint-imports`, `pytest` с PostgreSQL и RabbitMQ, chaos, `openspec validate --all --strict`
