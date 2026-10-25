## 1. Тесты (красные до правки)

- [x] 1.1 `infra/tests/main.tftest.hcl`: `api` и `worker` — образ `<репозиторий>@sha256:<digest>` без тега; ссылка без тега проходит как есть; порт реестра не срезается; `start_interval = "1s"`; отказ без digest остаётся. Мутация (`image = var.api_image`) роняет два прогона
- [x] 1.2 `tests/test_stand_ssh.py`: скрипт stand-ssh пишет `ControlMaster auto`, `ControlPersist` и короткий `ControlPath`; каждый `ssh -L` в `.github/` — с `-o ControlPath=none`. Мутации (без ControlMaster, туннель без ControlPath=none) роняют тест

## 2. Образ по digest

- [x] 2.1 Провайдер 4.6.0: `findImage` тянет образ, только если его нет локально; чтение пишет `Config.Image` — строку ссылки
- [x] 2.2 `infra/app.tf`: `local.api_image_ref`, контейнеры без `docker_image`, `removed` для `docker_image.api` и `docker_image.worker`
- [x] 2.3 `registry_auth`, `registry_username`, `registry_password` убраны: `versions.tf`, `variables.tf`, `deploy.yml`, `terraform.tfvars.example`, README

## 3. SSH и healthcheck

- [x] 3.1 `stand-ssh`: ControlMaster, ControlPath, ControlPersist; туннели `s3-backend` и `check-api.sh` с `-o ControlPath=none`
- [x] 3.2 `start_interval = "1s"`

## 4. Проверка

- [x] 4.1 `docker:29-dind`: тот же digest с новым тегом — `No changes`, контейнеры те же; новый digest — замер простоя до и после; переход со старого состояния — `removed` срабатывает, `api` и `worker` пересоздаются один раз, затем `No changes`
- [x] 4.2 Замер ssh с ControlMaster и без; туннели и `ExitOnForwardFailure` мимо master
- [x] 4.3 `infra/README.md`: образ без `docker_image`, `docker pull` перед локальным apply, ControlMaster
- [x] 4.4 `tofu fmt`/`validate`/`test`, shellcheck, zizmor, actionlint, ruff, mypy, pytest, `openspec validate --all --strict`
