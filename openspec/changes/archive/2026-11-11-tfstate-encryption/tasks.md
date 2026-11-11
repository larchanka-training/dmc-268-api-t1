# Tasks

## 1. Шаг 1: шифрование с fallback

- [x] 1.1 `infra/encryption.tf`: переменная `state_passphrase` (sensitive, не короче 16 символов), `key_provider "pbkdf2" "main"`, `method "aes_gcm" "main"`, `method "unencrypted" "legacy"`; `state` — `aes_gcm.main` с `fallback` на `unencrypted.legacy`, `plan` — `aes_gcm.main` без `fallback` (план создаётся и применяется в одном job'е, старых открытых планов нет)
- [x] 1.2 `deploy.yml`: `TF_VAR_state_passphrase` из `secrets.STATE_PASSPHRASE_DMC268_T1` в общем окружении шагов `tofu` и в шаге «Восстановить состояние OpenTofu»
- [x] 1.3 `infra/tests/*.tftest.hcl`: фиктивная фраза для `tofu test`; `tofu init -backend=false` и `tofu validate` в CI — без неё
- [x] 1.4 `.github/scripts/deploy/recover-state.sh`: расшифровать `errored.tfstate` во временном каталоге (копия `encryption.tf`, локальный бэкенд, `tofu init`, `tofu state pull`) в открытый файл с `umask 077`, удаляемый на выходе; сверка и `state push` — с ним; тесты восстановления
- [x] 1.5 `infra/README.md`: «Шифрование состояния», ручное восстановление с расшифровкой, `state_passphrase` в таблице переменных
- [x] 1.6 OpenSpec change `tfstate-encryption`, delta на `backend-delivery`

## 2. Ручные шаги шага 1

- [x] 2.1 Создать фразу `P=$(openssl rand -base64 48)`, сохранить `P` вне GitHub, затем `printf %s "$P" | gh secret set STATE_PASSPHRASE_DMC268_T1 --env stand`
- [x] 2.2 До первого деплоя с фразой: копия открытого состояния снята и лежит на сервере (#147: снята после мёржа, до установки секрета — деплои до него падали на `tofu init`, состояние не менялось)
- [x] 2.3 После первого деплоя: объект `api/terraform.tfstate` в бакете — конверт с `encrypted_data`, а не JSON состояния (проверено 2026-11-11 по тому `dmc268-s3-data`: все 10 версий после установки фразы, включая текущую, — `encrypted_data` без `resources`)
- [x] 2.4 После 2.3: проверить копию фразы вне GitHub — `export TF_VAR_state_passphrase=<копия>` отдельной строкой (префикс перед `init && …` до `state pull` не доходит, и тот взял бы прежний `export`), затем `cd infra && tofu init && tofu state pull | jq .serial`. До первого деплоя проверка ничего не значит: состояние открыто, и `fallback` читает его с любой фразой (проверено 2026-11-11: копией фразы вне GitHub расшифрована текущая версия объекта, serial 248, 52 ресурса; неверная фраза — «message authentication failed»)
- [x] 2.5 После 2.4: удалить копии открытого состояния из 2.2 — на сервере и локальные (проверено 2026-11-11: по `lineage` состояния на диске сервера вне тома хранилища и на ноутбуке копий нет)

## 3. Шаг 2: обязательное шифрование — отменён

Решение в #147: хранилище доступно только с сервера, а оба пути к нему (SSH, секреты GitHub) открывают те же секреты и без состояния. `fallback` и открытые версии, записанные до шифрования, остаются; почему и когда шаг 2 возвращается — `design.md`, «Шаг 2 отменён».

## 4. Закрытие

- [x] 4.1 `infra/README.md` и `infra/encryption.tf`: шаг 2 отменён, открытые версии удалить до выноса хранилища или его копии за пределы сервера
- [x] 4.2 Синхронизировать спеку и архивировать change
