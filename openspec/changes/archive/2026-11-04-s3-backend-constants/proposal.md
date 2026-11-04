# Proposal

## Why

`.github/actions/s3-backend` принимает девять inputs, а вызывающий у него один, job `deploy` в `deploy.yml`: второй, `tfstate-backend.yml`, удалён в `audit-fixes`. Семь inputs всегда получают одно и то же значение. Первый шаг action проверяет `~/.ssh/config`, хотя порядок шагов задан тем же единственным `deploy.yml`. Аудит 2026-11-04, #346.

Заодно образ хранилища отстаёт на патч: на стенде `rustfs/rustfs:1.0.3`, последний релиз 1.0.4 (2026-10-25) обновляет доверенные корневые сертификаты и зависимости.

## What Changes

- Имя контейнера, том, порт, образ, имена переменных учётных данных и бакет становятся константами в action. Inputs остаются только у логина и пароля: composite action сам секреты не читает.
- Шаг «Проверить SSH-подключение» удаляется.
- Образ хранилища `rustfs/rustfs:1.0.3` → `1.0.4`. Скрипт хранилища уже пересоздаёт контейнер на прежнем томе, когда образ меняется.

## Capabilities

### New Capabilities

Нет.

### Modified Capabilities

Нет.

`skip_specs: true`: меняется обвязка деплоя, а не поведение сервиса.

## Impact

- `.github/actions/s3-backend/action.yml`, `.github/workflows/deploy.yml`.
- Первый деплой после мёржа пересоздаст контейнер `dmc268-s3` на томе `dmc268-s3-data`: в summary `S3_IMAGE=обновлён`.
- Хранилище общее с деплоем UI, а группы `concurrency: deploy` у API и UI свои. Мёржить, когда деплой UI не идёт (`gh run list -R larchanka-training/dmc-268-ui-t1 --workflow deploy.yml -s in_progress`), иначе UI потеряет хранилище посреди записи state.
