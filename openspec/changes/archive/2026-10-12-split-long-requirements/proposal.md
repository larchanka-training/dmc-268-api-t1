# Proposal

## Why

Ошибки в спеках всплывают только при архиве change: CI спеки не проверяет. На `develop` `openspec validate --all --strict` падает на 8 из 13 элементов (#52):

- 21 требование в 7 спеках длиннее 500 символов — порог зашит в OpenSpec (`MAX_REQUIREMENT_TEXT_LENGTH`), `--strict` превращает предупреждение в ошибку;
- change `align-review-pipeline-with-webhook-intake` и `align-review-job-with-job-queue` уже синхронизированы в `openspec/specs/` (в #37), но не заархивированы: архив отказался бы повторно применять их дельты.

## What Changes

- Длинные требования сокращены или разделены на требования по одному поведению; примеры и крайние случаи — в сценариях. Поведение не меняется: каждое SHALL / SHALL NOT сохранено, исходные сценарии перенесены дословно, новые сценарии только повторяют уже заданные утверждения.
- Два синхронизированных change заархивированы без повторного применения дельт (`--skip-specs`).
- CI: job `openspec` — `openspec validate --all --strict`, версия openspec закреплена.

## Capabilities

### Modified Capabilities

- `webhook-intake`, `review-data-model`, `job-queue`, `backend-delivery`, `backend-architecture`, `backend-base-setup`, `review-pipeline` — только раскладка текста требований.

## Impact

- `openspec/specs/` — после архива этого change.
- `.github/workflows/ci.yml` — новый job.
- Открытые PR со своими change: дельта #49 правит «Собранный контекст сохраняется даже как заготовка» в `review-pipeline` — это требование не тронуто.
