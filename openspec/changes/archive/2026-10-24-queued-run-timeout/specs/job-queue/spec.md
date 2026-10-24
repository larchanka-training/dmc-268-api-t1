## ADDED Requirements

### Requirement: Ждущий в очереди прогон выметается по своему порогу

Прогон в `queued` SHALL переводиться в `failed` выметателем только после `QUEUED_RUN_TIMEOUT_SECONDS` без прогресса (по умолчанию 6 ч), остальные нетерминальные прогоны — после `STALE_RUN_TIMEOUT_SECONDS`. Воркер SHALL NOT стартовать, если порог для `queued` не больше порога зависшего прогона.

#### Scenario: Хвост очереди не роняет ждущий прогон

- **WHEN** прогон в `queued` ждёт дольше `STALE_RUN_TIMEOUT_SECONDS`, но меньше `QUEUED_RUN_TIMEOUT_SECONDS`
- **THEN** проход оставляет его без изменений

#### Scenario: Задача, не дошедшая до брокера, не блокирует коммит навсегда

- **WHEN** прогон в `queued` не двигался дольше `QUEUED_RUN_TIMEOUT_SECONDS`
- **THEN** прогон переходит в `failed` с указанием причины

#### Scenario: Порог для очереди не длиннее порога зависшего прогона

- **WHEN** воркер стартует с `QUEUED_RUN_TIMEOUT_SECONDS`, не большим `STALE_RUN_TIMEOUT_SECONDS`
- **THEN** он завершается с ошибкой конфигурации до подключения к брокеру
