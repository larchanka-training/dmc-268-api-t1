# Proposal

## Why

После #204 простой API при выкате ≈ 12 с (деплой 40186922411), цель #159 — меньше 10 с. Весь остаток — «создать контейнер → healthy» (10,3 с), а из лога apply не видно, что в нём: удаление старого, создание по SSH, старт процесса или ожидание проверки. Summary деплоя — только образ и адрес: исход, шаги, план OpenTofu и место на диске видны лишь в логе. И ничто не мешает плану уничтожить том базы или брокера: apply применяет его не глядя (пересекается с #161). Закрывает #205.

## What Changes

- Полный выкат — планом в файл: `plan.sh` (`tofu plan -out=tfplan`, `tofu show -json` трубой в `scripts/deploy_report.py plan`) и `apply.sh` (`tofu apply tfplan`). Сторож: план с `delete` у `docker_volume.postgres` или `docker_volume.rabbitmq` (удаление или замена) роняет шаг до apply. План строится сразу после «Поднять базу и брокер», до миграций.
- `lifecycle { prevent_destroy = true }` у `docker_volume.postgres` и `docker_volume.rabbitmq` (ревью #210): targeted apply шага «Поднять базу и брокер» тянет тома зависимостью контейнеров и мимо сторожа заменял том. Часть #161 (тома); `tofu destroy` теперь падает на томах — снести стенд можно, только сняв `prevent_destroy`.
- `apply.sh` собирает сырьё для разбивки простоя: id старого контейнера API, поток `docker events` по нему всё время apply, `docker inspect` нового (только `Id`, `Created`, `State`). Готовность нового — событие `health_status: healthy` из потока, `State.Health.Log` — запасной путь с пометкой о недостоверности. Вопросы серверу — под `timeout 30`; сбой ssh записывается рядом с ответом, и summary отличает его от «контейнера нет».
- `migrate.sh` копирует вывод alembic в каталог отчёта: summary считает строки `Running upgrade A -> B`.
- `summary.sh` (`if: always()`, `timeout-minutes: 3`, ssh под `timeout 30`): свободное место `df -Pk` на `/` и в каталоге Docker, затем `deploy_report.py render` — исход и шаг падения, простой API по этапам, план по типам ресурсов, миграции, диск, длительность шагов. Подписи — ключи OpenTelemetry, свои — `dmc268.deploy.*`.
- Длительность шагов — засечки `mark.sh` первой строкой каждого шага с `run`; исходы шагов — списком `STEP_RESULTS` в env шага summary. У каждого шага job'а `deploy` — id.
- `stand-ssh`: `ServerAliveInterval 10`, `ServerAliveCountMax 3` — мёртвый master закрывается за 30 с. Копия во фронте (`dmc-268-ui-t1`) правится отдельно.
- `setup-opentofu` без обёртки (`tofu_wrapper: false`): она копирует stdout tofu в выходы шага, а это `output -raw migrate_env` и JSON плана с паролями.
- Вход в ghcr — до диагностики, в группе подготовки: так шаги с `uses` идут подряд и их время собирается в одну строку.

## Capabilities

### Modified Capabilities

- `backend-delivery`: развёртывание не уничтожает тома данных; развёртывание отчитывается о выкате; отчёт о выкате достоверен и не задерживает развёртывание.

## Impact

- `.github/workflows/deploy.yml`, `.github/actions/stand-ssh`, `.github/scripts/deploy/` (`plan.sh`, `apply.sh`, `summary.sh`, `mark.sh` — новые; `migrate.sh`), `scripts/deploy_report.py`, `infra/postgres.tf`, `infra/rabbitmq.tf`.
- Тесты: `tests/test_deploy_report.py`, фикстуры `tests/fixtures/deploy/`, `tests/test_stack_descriptions.py` (полный apply теперь в `apply.sh`).
- `infra/README.md`, «Деплой».
- Права токена не меняются: `actions: read` не нужен.
- Главная проверка — первый деплой после мёржа: SSH-поток событий через ControlMaster и `df` на сервере в CI ещё не шли.
