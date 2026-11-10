# Proposal

## Why

#147. Состояние OpenTofu (`infra/backend.tf`, бакет `dmc268-tfstate`, ключ `api/terraform.tfstate`) лежит в хранилище открытым JSON: в нём пароли базы, брокера и реплики, ключ GitHub App, секрет вебхука, ключ шифрования токенов пользователя. Доступ к бакету — это все секреты стенда сразу. Так же открыт `infra/errored.tfstate`, который остаётся на раннере после упавшей записи состояния. OpenTofu умеет шифровать состояние и план сам (блок `terraform { encryption { … } }`, `key_provider "pbkdf2"`, `method "aes_gcm"`).

Переход в два шага: сейчас в бакете открытое состояние, и первый деплой с шифрованием должен его прочитать. Шаг 1 — шифрование с запасным чтением открытого состояния (`fallback`), шаг 2 — обязательное шифрование без запасного пути и удаление старых открытых версий объекта.

## What Changes

Шаг 1 (этот пул-реквест):

- `infra/encryption.tf` (новый): переменная `state_passphrase` (sensitive, не короче 16 символов — минимум `pbkdf2`), `key_provider "pbkdf2" "main"`, `method "aes_gcm" "main"`, `method "unencrypted" "legacy"`; у `state` — `aes_gcm.main` с `fallback` на `unencrypted.legacy`, у `plan` — `aes_gcm.main` без `fallback` (план создаётся и применяется в одном job'е). Переменная лежит в том же файле, чтобы он был самодостаточным: восстановление копирует только его.
- `deploy.yml`: `TF_VAR_state_passphrase` из секрета `STATE_PASSPHRASE_DMC268_T1` (environment `stand`) — в общем окружении шагов `tofu` и в шаге «Восстановить состояние OpenTofu»: фраза нужна уже `tofu init`.
- CI: `tofu init -backend=false` и `tofu validate` идут без фразы, `tofu test` получает фиктивную в `infra/tests/*.tftest.hcl`.
- `.github/scripts/deploy/recover-state.sh`: `tofu state push` зашифрованный файл не читает, поэтому `errored.tfstate` сначала расшифровывается во временном каталоге (копия `infra/encryption.tf` и файл как локальный `terraform.tfstate`, `tofu init` и `tofu state pull`) во временный открытый файл (`umask 077`, удаляется на выходе); сверка serial, lineage и ресурсов и запись — с ним. В бакете состояние остаётся зашифрованным: push шифрует.
- `infra/README.md`: раздел «Шифрование состояния» (где фраза, локальная работа, проверка бакета, ротация, откат, потеря фразы), ручное восстановление с расшифровкой, строка `state_passphrase` в таблице переменных.

Шаг 2 (отдельный пул-реквест, после того как деплой с шагом 1 переписал состояние; задачи открыты в `tasks.md`): `enforced = true`, убрать `fallback` у `state` и `unencrypted.legacy`, удалить старые открытые версии объекта в бакете и локальную копию состояния. До шага 2 change не архивируется.

## Capabilities

### New Capabilities

### Modified Capabilities

- `backend-delivery`: состояние развёртывания хранится зашифрованным; восстановление после обрыва записывает зашифрованное несохранённое состояние.

## Impact

- `infra/encryption.tf` (новый), `infra/tests/*.tftest.hcl`, `.github/workflows/deploy.yml`, `.github/scripts/deploy/recover-state.sh`, тесты восстановления, `infra/README.md`.
- Новый секрет `STATE_PASSPHRASE_DMC268_T1` в environment `stand` — заводится вручную до мёржа, копия фразы — вне GitHub: без фразы состояние не прочитать.
- Первый деплой после мёржа читает открытое состояние и переписывает его зашифрованным (любой `apply` переписывает, даже без изменений). До этого деплоя в бакете лежит открытая версия; старые версии объекта остаются до шага 2.
- Локальная работа с `infra/` против настоящего хранилища требует фразы (`TF_VAR_state_passphrase`) уже на `tofu init`; проверка конфигурации без состояния (`init -backend=false`, `validate`) — нет.
- `tofu show -json` плана выводит значение фразы в `variables`, как и остальные секреты `TF_VAR_*`; `scripts/deploy_report.py` берёт из него только счётчики и ничего не сохраняет.
