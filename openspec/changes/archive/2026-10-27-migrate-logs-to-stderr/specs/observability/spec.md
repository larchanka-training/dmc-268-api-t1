# Spec Delta

## MODIFIED Requirements

### Requirement: Логи — JSON с trace-контекстом

Логи API, воркера и миграций SHALL писаться stdlib `logging` одной JSON-строкой. Формат
SHALL следовать модели OTel: `trace_id`, `span_id`, `trace_flags`, `severity`, `service.name`,
`logger` — на верхнем уровне, поля из `extra` — атрибутами записи. Бизнес-контекст SHALL нести
те же имена, что атрибуты спанов, и SHALL NOT вклеиваться в текст. Настройка логов SHALL жить в
одном модуле инфраструктуры; прикладной код SHALL использовать только
`logging.getLogger(__name__)`.

#### Scenario: Запись внутри спана

- **WHEN** обработчик этапа прогона пишет лог
- **THEN** у записи `trace_id` и `span_id` спана этапа, а `review_run_id` — атрибут записи

#### Scenario: Запись вне спана

- **WHEN** модуль пишет лог вне любого спана
- **THEN** запись выходит без `trace_id` и `span_id` и не теряется

#### Scenario: Источники записи различимы

- **WHEN** в Loki рядом стоят записи `uvicorn.access` и обработчика воркера
- **THEN** они различимы по полю `logger` с именем логгера записи

## ADDED Requirements

### Requirement: Поток логов

API и воркер SHALL писать JSON-логи в stdout. Миграции SHALL писать их в stderr: stdout alembic
CLI — результат команды (`current`, `heads`), его читают скрипты деплоя.

#### Scenario: stdout миграций — только результат команды

- **WHEN** деплой спрашивает `alembic current` и читает ревизию из stdout
- **THEN** stdout несёт только ревизию, а JSON-записи логов миграций уходят в stderr
