# Tasks

## 1. Шаг 1: шифрование с fallback

- [x] 1.1 `infra/encryption.tf`: переменная `state_passphrase` (sensitive, не короче 16 символов), `key_provider "pbkdf2" "main"`, `method "aes_gcm" "main"`, `method "unencrypted" "legacy"`; `state` — `aes_gcm.main` с `fallback` на `unencrypted.legacy`, `plan` — `aes_gcm.main` без `fallback` (план создаётся и применяется в одном job'е, старых открытых планов нет)
- [x] 1.2 `deploy.yml`: `TF_VAR_state_passphrase` из `secrets.STATE_PASSPHRASE_DMC268_T1` в общем окружении шагов `tofu` и в шаге «Восстановить состояние OpenTofu»
- [x] 1.3 `infra/tests/*.tftest.hcl`: фиктивная фраза для `tofu test`; `tofu init -backend=false` и `tofu validate` в CI — без неё
- [x] 1.4 `.github/scripts/deploy/recover-state.sh`: расшифровать `errored.tfstate` во временном каталоге (копия `encryption.tf`, локальный бэкенд, `tofu init`, `tofu state pull`) в открытый файл с `umask 077`, удаляемый на выходе; сверка и `state push` — с ним; тесты восстановления
- [x] 1.5 `infra/README.md`: «Шифрование состояния», ручное восстановление с расшифровкой, `state_passphrase` в таблице переменных
- [x] 1.6 OpenSpec change `tfstate-encryption`, delta на `backend-delivery`

## 2. Ручные шаги шага 1

- [ ] 2.1 Создать фразу `P=$(openssl rand -base64 48)`, сохранить `P` вне GitHub, затем `printf %s "$P" | gh secret set STATE_PASSPHRASE_DMC268_T1 --env stand`
- [ ] 2.2 До мёржа: `tofu state pull > backup` локально, копию хранить вне репозитория
- [ ] 2.3 После первого деплоя: объект `api/terraform.tfstate` в бакете — конверт с `encrypted_data`, а не JSON состояния (`jq 'keys'`)
- [ ] 2.4 После 2.3: проверить копию фразы вне GitHub — `TF_VAR_state_passphrase=<копия> tofu init && tofu state pull | jq .serial`. До первого деплоя проверка ничего не значит: состояние открыто, и `fallback` читает его с любой фразой

## 3. Шаг 2: обязательное шифрование (отдельный пул-реквест)

- [ ] 3.1 Проверить версионирование объектов в RustFS (`list-object-versions`, `delete-object --version-id`) на стенде
- [ ] 3.2 `infra/encryption.tf`: убрать `fallback` у `state` и `method "unencrypted" "legacy"`, `enforced = true` у `state` и `plan`
- [ ] 3.3 Проверить `recover-state.sh` с `enforced = true` и без `fallback`: `state push` принимает открытый расшифрованный файл, и бэкенд пишет его снова зашифрованным (`jq 'keys'` → `encrypted_data`); тест восстановления — на этот случай
- [ ] 3.4 Удалить старые открытые версии объекта `api/terraform.tfstate` в бакете
- [ ] 3.5 Удалить локальную копию состояния из 2.2 — только после 2.4
- [ ] 3.6 Синхронизировать спеку и архивировать change
