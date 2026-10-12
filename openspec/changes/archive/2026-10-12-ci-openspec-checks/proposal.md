# Proposal

## Why

CI не проверяет спеки: ошибка в дельте всплывает только при архиве, а change с закрытыми задачами забывают архивировать — на `develop` так висели три change, и дельты одного из них не дошли до `openspec/specs/` (#52).

## What Changes

- Job `openspec` в `ci.yml`: `openspec validate --all --strict`, версия openspec закреплена.
- `scripts/check_openspec_archived.py`: падает, если change со всеми закрытыми задачами не заархивирован; открытые change выводятся в summary прогона.
- AGENTS.md: `openspec validate --all --strict` в проверках перед коммитом, архив change — в том же PR.

## Capabilities

Поведение сервиса не меняется, спеки не правятся (`skip_specs`).

## Impact

- `.github/workflows/ci.yml`, `scripts/check_openspec_archived.py`, `AGENTS.md`.
