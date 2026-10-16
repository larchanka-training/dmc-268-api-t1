# Tasks

## 1. Спеки

- [x] 1.1 Сократить или разделить 21 требование длиннее 500 символов: `webhook-intake` (9), `review-data-model` (4), `backend-delivery` (3), `review-pipeline` (2), `job-queue`, `backend-architecture`, `backend-base-setup` (по 1)
- [x] 1.2 Сверить «было → стало»: каждое SHALL / SHALL NOT на месте, исходные сценарии дословно
- [x] 1.3 Заархивировать с `--skip-specs` уже синхронизированные `align-review-pipeline-with-webhook-intake` и `align-review-job-with-job-queue`

## 2. CI

- [x] 2.1 Job `openspec` в `ci.yml`: `npx @fission-ai/openspec@1.14.1 validate --all --strict`

## 3. Приёмка

- [x] 3.1 Пробный архив этого change в копии: дельты применяются, `validate --all --strict` проходит
- [x] 3.2 После архива в этом же PR `validate --all --strict` проходит на всём дереве — это и проверяет job `openspec`
