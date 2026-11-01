## Why

Кабинет не может убрать мок настроек репозитория (`src/app/mocks/repositorySettings.ts`,
dmc-268-ui-t1#40), пока `GET`/`PUT /api/v1/repositories/{repository_id}/settings` и
`GET /api/v1/repositories/{repository_id}/rules` отвечают `501`. Контракт этих ручек опубликован
заглушками (#113) и уже обещает, что `auto_review_enabled` включает ревью по вебхуку, а
`min_severity` задаёт порог merge gate, — сейчас ни то, ни другое не работает. Задача #272.

## What Changes

- `GET …/settings` отдаёт настройки ревью репозитория из его записи; у репозитория, которому
  настройки не сохраняли, — значения по умолчанию `auto_review_enabled = true`,
  `branch_filter = []`, `min_severity = critical`.
- `PUT …/settings` заменяет настройки целиком; повтор того же тела ничего не меняет. Право — роль
  admin или maintain у GitHub, проверенная запросом мимо кэша видимости, как у
  `DELETE /repositories/{id}`; без роли — `403 REPOSITORY_MANAGE_REQUIRED`.
- `GET …/rules` отдаёт исход загрузки `.review/rules.md` с ветки по умолчанию: ветка
  разрешается в sha новым методом `VcsGateway`, файл грузится тем же `load_repo_rules`, что у
  прогона, с кэшем по sha; в значении кэша хранится `fetched_at`.
- Хранение: миграция `0016` добавляет `repositories.branch_filter` (`text[]`) и
  `repositories.min_severity` (существующий enum `finding_severity`), обе `NOT NULL` с
  `server_default`; у `auto_review_enabled` появляется `server_default true` — как
  умолчание контракта.
- Применение настроек — частично:
  - `auto_review_enabled = false` — вебхук `pull_request` не создаёт прогон и ставит на head
    проверку `success` со своим текстом «Авторевью выключено в настройках» (у выключенного
    репозитория — «Ревью выключено в сервисе»); вебхук закрытия треда — тоже;
  - `min_severity` — порог merge gate: блокирует незакрытое замечание с severity не ниже порога,
    а не только `critical`;
  - `branch_filter` только хранится; описание поля в контракте говорит, что фильтр пока не
    применяется. Применение — отдельная задача.
- Контракт: формы тел запроса и ответов не меняются. Из `responses` трёх ручек уходит `501`,
  добавляются `502 SCM_UNAVAILABLE` (сбой GitHub на проверке видимости и роли), у `GET …/rules` —
  ещё `404 REPOSITORY_NOT_IN_INSTALLATION` (приложение не установлено, как у диффа прогона); описания ручек и
  поля `branch_filter` — реальное поведение. Экспорт `docs/openapi/openapi.json` обновляется.

## Capabilities

### New Capabilities

### Modified Capabilities

- `review-rest-api`: требования-заглушки «Настройки репозитория описаны заглушкой», «Тело записи
  настроек проверяется раньше 501» и «Статус правил репозитория описан заглушкой» заменяются
  реальным поведением ручек.
- `review-data-model`: добавляется требование о хранении настроек ревью в записи репозитория.
- `webhook-intake`: добавляется требование о выключенном автоматическом ревью
  (`auto_review_enabled = false`).
- `review-pipeline`: «Merge gate по критичным открытым тредам» — порог из `min_severity`
  репозитория вместо постоянного `critical`.
- `repo-rules`: добавляется требование о чтении правил с ветки по умолчанию для кабинета; в кэше
  правил хранится время получения файла.

## Impact

- Код: `app/api/repositories.py` (три ручки), `app/application/use_cases/` (новый use case
  настроек и статуса правил), `app/application/repo_config_loader.py` (`fetched_at`, ключ кэша
  `v2`), `app/application/ports/vcs_gateway.py` и `app/infrastructure/vcs/github.py` (новый метод
  разрешения ветки в sha), `app/domain/merge_gate.py`, `app/application/merge_gate_publish.py`,
  `app/application/publish_review.py`, `app/application/sync_thread_resolution.py`,
  `app/application/use_cases/handle_webhook.py`, `app/domain/entities.py`,
  `app/infrastructure/db/{models,mappers,repositories}.py`.
- База: ревизия Alembic `0016`, `docs/erd.md`.
- Контракт: `docs/openapi/openapi.json` (экспорт).
- Зависит от PR #308 (`feat/review-run-diff`): `InstallationCatalog.repository_installation_id` с
  кэшем и `Container.cabinet_vcs_gateway()`.
- Пересечение с активным change `repositories-one-list`: тот же `review-rest-api`, но другие
  требования; в `webhook-intake` и `review-data-model` тот change правит существующие требования,
  этот — только добавляет новые.
