# Proposal

## Why

Actions в `ci.yml` и `deploy.yml` работают на Node 20, который на раннерах GitHub объявлен устаревшим: каждый прогон печатает предупреждение, а после отключения Node 20 они перестанут запускаться. Прогоны по устаревшим коммитам пул-реквеста доигрывают до конца и занимают раннеры. У токена CI права по умолчанию, у джоб нет таймаутов: зависший прогон живёт шесть часов.

## What Changes

- `ci.yml`: `concurrency` — новый пуш в ветку пул-реквеста отменяет прошлый прогон; пуши в `main` и `develop` не отменяются.
- `ci.yml`: `permissions: contents: read`.
- `timeout-minutes` у каждой джобы `ci.yml` и `deploy.yml`.
- Actions на Node 24: `checkout` v7, `setup-uv` v10.2.0, `setup-opentofu` v2, `setup-buildx` v4, `login` v4, `build-push` v7. `setup-uv` закреплён точной версией: плавающих мажорных тегов у него нет.
- Комментарии и сообщения в `ci.yml` переведены на русский.

**Не входит:** распараллеливание джоб, удаление `syntax`, кеш mypy и `ruff format --check` — рассмотрены и отложены.

## Capabilities

### New Capabilities

Нет.

### Modified Capabilities

Нет.

`skip_specs: true`: меняется инструментарий CI, а не поведение сервиса.

## Impact

- `.github/workflows/ci.yml`, `.github/workflows/deploy.yml`.
- Мажорные версии docker-action в `deploy.yml` впервые отработают на деплое после мёржа: на пул-реквесте деплой не запускается.
