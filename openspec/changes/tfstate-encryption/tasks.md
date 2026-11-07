# Tasks

## 1. Шаг 1: шифрование с fallback

- [x] 1.1 `infra/encryption.tf`: переменная `state_passphrase` (sensitive, не короче 16 символов), `key_provider "pbkdf2" "main"`, `method "aes_gcm" "main"`, `method "unencrypted" "legacy"`; `state` и `plan` — `aes_gcm.main` с `fallback` на `unencrypted.legacy`
- [x] 1.2 `deploy.yml`: `TF_VAR_state_passphrase` из `secrets.STATE_PASSPHRASE_DMC268_T1` в общем окружении шагов `tofu` и в шаге «Восстановить состояние OpenTofu»
- [x] 1.3 `infra/tests/*.tftest.hcl`: фиктивная фраза для `tofu test`; `tofu init -backend=false` и `tofu validate` в CI — без неё
- [x] 1.4 `.github/scripts/deploy/recover-state.sh`: расшифровать `errored.tfstate` во временном каталоге (копия `encryption.tf`, локальный бэкенд, `tofu init`, `tofu state pull`) в открытый файл с `umask 077`, удаляемый на выходе; сверка и `state push` — с ним; тесты восстановления
- [x] 1.5 `infra/README.md`: «Шифрование состояния», ручное восстановление с расшифровкой, `state_passphrase` в таблице переменных
- [x] 1.6 OpenSpec change `tfstate-encryption`, delta на `backend-delivery`

## 2. Ручные шаги шага 1

- [ ] 2.1 Создать секрет `STATE_PASSPHRASE_DMC268_T1` в environment `stand` (`openssl rand -base64 32`), копию фразы положить вне GitHub
- [ ] 2.2 До мёржа: `tofu state pull > backup` локально, копию хранить вне репозитория
- [ ] 2.3 После первого деплоя: объект `api/terraform.tfstate` в бакете — конверт с `encrypted_data`, а не JSON состояния (`jq 'keys'`)

## 3. Шаг 2: обязательное шифрование (отдельный пул-реквест)

- [ ] 3.1 Проверить версионирование объектов в RustFS (`list-object-versions`, `delete-object --version-id`) на стенде
- [ ] 3.2 `infra/encryption.tf`: убрать `fallback` и `method "unencrypted" "legacy"`, `enforced = true` у `state` и `plan`
- [ ] 3.3 Удалить старые открытые версии объекта `api/terraform.tfstate` в бакете
- [ ] 3.4 Удалить локальную копию состояния из 2.2
- [ ] 3.5 Синхронизировать спеку и архивировать change
