# Proposal

## Why

Деплой 40182488875 (#202): простой API около 19 с при DoD #159 «меньше 10 с» (#203). Из них 6 с — замена `docker_image.api` уже после остановки старого API: тег в имени образа — commit sha, новый на каждом коммите, поэтому ресурс образа, а за ним и контейнеры пересоздаются даже при том же digest. Ещё около 4 с — вызовы Docker API по ssh: каждый запрос провайдера — новое SSH-соединение. До 2 с — `start_interval` healthcheck.

`create_before_destroy` у образов проверен на отдельном демоне Docker 29.9.2 и почти ничего не даёт (7,2 → 6,4 с): OpenTofu всё равно гасит старый контейнер первым.

## What Changes

- `infra/app.tf`: ресурсов `docker_image.api` и `docker_image.worker` больше нет. Контейнеры `api` и `worker` ссылаются на образ строкой `<репозиторий>@sha256:<digest>` (`local.api_image_ref`, тег из `api_image` срезан); ресурсы образов уходят из состояния блоками `removed` без уничтожения. Образ скачивает `migrate.sh` по той же ссылке до полного apply.
- `infra/versions.tf`, `infra/variables.tf`: у провайдера нет `registry_auth`, переменных `registry_username` и `registry_password` нет — образ из приватного пакета тянет только `migrate.sh` через `docker login` на раннере. `deploy.yml` больше не передаёт `GITHUB_TOKEN` в `tofu`.
- `.github/actions/stand-ssh`: `ControlMaster auto`, `ControlPath ~/.ssh/cm-%C`, `ControlPersist 10m` у хоста `stand`. Туннели `s3-backend` и `check-api.sh` — своим соединением (`-o ControlPath=none`).
- Healthcheck API: `start_interval = "1s"`.
- Тесты: `infra/tests/main.tftest.hcl` (ссылка без тега, порт реестра, `start_interval`), `tests/test_stand_ssh.py` (ControlMaster в конфиге, туннели мимо master).

## Capabilities

### Modified Capabilities

- `backend-delivery`: «Стенд запускает образ API по digest» — новый тег того же образа не пересоздаёт `api` и `worker`.

## Impact

- `infra/app.tf`, `infra/versions.tf`, `infra/variables.tf`, `infra/terraform.tfvars.example`, `infra/README.md`, `infra/tests/main.tftest.hcl`.
- `.github/actions/stand-ssh/action.yml`, `.github/actions/s3-backend/action.yml`, `.github/scripts/deploy/check-api.sh`, `.github/scripts/deploy/migrate.sh` (комментарий), `.github/workflows/deploy.yml`.
- `tests/test_stand_ssh.py`.
- Первый деплой после мёржа пересоздаёт `api` и `worker` один раз даже при том же digest: в состоянии у них `image` — ID образа, в конфигурации — ссылка. Воркер при этом дренирует сообщение в работе, API его не ждёт.
- Локальный `tofu apply` требует `docker pull <api_image>` до запуска: без учётных данных провайдер приватный образ не скачает.
