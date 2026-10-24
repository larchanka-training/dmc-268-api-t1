# Стек сервера (API на Python 3.14, PostgreSQL 18, RabbitMQ 4.3)

Нужны **Docker Engine** на целевом хосте и OpenTofu >= 1.12 (в CI ставится 1.12.6). Образ API не собирается здесь: он публикуется в ghcr из CI и забирается по тегу (`api_image`).

## Локальный или удалённый Docker

Скопируйте `terraform.tfvars.example` в `terraform.tfvars` и замените плейсхолдеры `CHANGE_ME_*`. Файл `terraform.tfvars` не коммитится (gitignore).

- Локальный демон: `docker_host = "unix:///var/run/docker.sock"` (у Docker Desktop на macOS часто `unix:///Users/<вы>/.docker/run/docker.sock`).
- Удалённый сервер: `docker_host = "ssh://user@server"` (нужны SSH-доступ и Docker на удалённом хосте).

PostgreSQL и RabbitMQ публикуются на `internal_bind_ip`, API — на `bind_ip`; в деплое оба равны `127.0.0.1`. Другого значения конфигурация не примет: обе переменные проверяются на адрес петли (`127.0.0.0/8` или `::1`), и ошибка всплывает на `plan`. В URL для клиентов используется `service_host`, его нельзя ставить в `0.0.0.0`.

## Ограничения контейнеров

- `api` и `migrate` работают не от root (`USER` в `Dockerfile`), с файловой системой только для чтения (запись — только в `/tmp`), без capabilities и с `no-new-privileges`.
- `postgres` и `rabbitmq` — только `no-new-privileges`: их entrypoint стартует от root, чтобы подготовить каталог данных, и сам переходит на служебного пользователя.
- У каждого контейнера потолок памяти без свопа (`postgres` 1 ГБ, `rabbitmq`, `api` и `worker` по 512 МБ, `migrate` 256 МБ) и ротация логов: три файла по 10 МБ.

Смена любого из этих атрибутов пересоздаёт контейнер. Данные PostgreSQL и RabbitMQ лежат в томах и замену переживают.

## Версия провайдера

Точная версия `kreuzwerker/docker` и её хэши закреплены в `.terraform.lock.hcl`, файл коммитится. После смены `version` в `versions.tf` его пересобирают на все платформы команды, иначе `tofu init` на неперечисленной системе допишет в файл свои хэши и оставит изменение в рабочей копии:

```bash
cd infra && tofu init -backend=false -upgrade && tofu providers lock \
  -platform=linux_amd64 -platform=linux_arm64 \
  -platform=darwin_amd64 -platform=darwin_arm64 \
  -platform=windows_amd64
```

## Деплой

Стенд катится сам при мерже в `develop` (`.github/workflows/deploy.yml`). `main` в деплое не участвует: сервер один, и два источника дрались бы за него.

Порядок такой: образ собирается и публикуется в `ghcr.io/larchanka-training/dmc-268-api-t1` с тегом по commit sha → поднимается хранилище состояния и туннель к нему → `tofu apply` по ключу CI → проверка `/health` через туннель. Публичной проверки здесь нет: API снаружи не публикуется, а его доступность по `https://62.169.24.237/api/v1/` проверяет деплой интерфейса.

**Снаружи API адресуется через интерфейс, с исходным путём.** Публичный вход — `nginx` в контейнере фронтенда на **https://62.169.24.237** (сертификат Let's Encrypt на IP, HTTP перенаправляется на HTTPS; устройство — в `infra/README.md` репозитория `dmc-268-ui-t1`). Префикс он не трогает: браузерный `GET /api/v1/reviews` приходит сюда как `GET /api/v1/reviews`, поэтому роутеры монтируются на `/api/v1`, как требует контракт API. `/health` через этот вход не публикуется — проверка живости ходит мимо него.

**Миграции применяются отдельным контейнером** `dmc268-migrate`, от которого зависит API. Он ждёт, пока база начнёт принимать соединения, выполняет `alembic upgrade head` и завершается; `postcondition` на его код возврата останавливает деплой до старта API, если миграция упала. Проверка `/health` этого не заметила бы: по контракту она отвечает независимо от базы.

**Воркер** `dmc268-worker` разворачивается из того же образа, что и API, с командой `python -m app.worker`. Портов у него нет: с остальным стеком он говорит только через очередь и базу, наружу — исходящими запросами к GitHub API (дифф, `.review/rules.md`, файлы для AST), поэтому получает те же `GITHUB_APP_ID` и `GITHUB_APP_PRIVATE_KEY`, что и API. Стартует, как и API, после миграций. HTTP-проверки у него нет, поэтому деплой считает воркер живым, если контейнер 30 секунд подряд запущен и ни разу не перезапустился.

Адаптер модели воркера задаёт `llm_backend` (`LLM_BACKEND`): `stub` - без похода к модели, `ollama` - локальная модель. Значения по умолчанию нет: заглушка выбирается только явно, и деплой стенда передаёт `TF_VAR_llm_backend: stub` в `.github/workflows/deploy.yml`.
`ollama` требует `ollama_model` и развёрнутой Ollama по `ollama_base_url`.
Eurorouter подключается только фоллбэком после локальной модели и только при `llm_hosted_fallback = true` с `eurorouter_base_url`, `eurorouter_model` и `eurorouter_api_keys`: это решение отправляет код проверяемых репозиториев hosted-провайдеру.

Переменные, без которых `apply` не пройдёт (в CI приходят через `TF_VAR_*`):

| Переменная | Откуда |
| --- | --- |
| `api_image` | тег собранного образа |
| `postgres_password`, `rabbitmq_password` | секреты `PG_PASSWORD_DMC268_T1`, `RABBITMQ_PASSWORD_DMC268_T1` |
| `grafana_admin_password` | секрет `GRAFANA_ADMIN_PASSWORD_DMC268_T1` — пароль админа Grafana (`GF_SECURITY_ADMIN_PASSWORD` контейнера `dmc268-grafana`). Значения по умолчанию у переменной нет: без секрета `apply` падает на её чтении. Ставится в GitHub вручную, отдельным шагом (не код): `openssl rand -base64 24 | gh secret set GRAFANA_ADMIN_PASSWORD_DMC268_T1 -R larchanka-training/dmc-268-api-t1` — или своим паролем; локальному стеку он не нужен, Grafana там без логина |
| `postgres_monitor_password` | секрет `PG_MONITOR_PASSWORD_DMC268_T1` — пароль роли `dmc268_monitor` (`pg_monitor`), которую создаёт миграция 0011 и под которой коллектор снимает метрики базы (`POSTGRESQL_PASSWORD` его env). Значения по умолчанию у переменной нет: без секрета `apply` падает на её чтении. Ставится в GitHub вручную, отдельным шагом (не код): `openssl rand -base64 24 | gh secret set PG_MONITOR_PASSWORD_DMC268_T1 -R larchanka-training/dmc-268-api-t1` |
| `github_webhook_secret` | секрет `WEBHOOK_SECRET_DMC268_T1` |
| `user_token_encryption_key` | секрет `USER_TOKEN_KEY_DMC268_T1` — 32 байта в base64 (`openssl rand -base64 32`). Без него или с битым значением `apply` падает на проверке переменной: стенд не стартует без шифрования токенов пользователя. Генерация и установка — `README.md`, «Ключ шифрования токенов пользователя» |
| `registry_username`, `registry_password` | `github.actor` и `GITHUB_TOKEN`, живут один прогон |
| `docker_host`, `service_host` | собираются из `VPS_DMC268_U` и `VPS_DMC268_IP_T1` |
| `bind_ip` | `127.0.0.1`: API снаружи не публикуется, к нему ходит интерфейс изнутри docker-сети |
| `llm_backend` | `stub` прямо в `deploy.yml`: модели на стенде пока нет |

Без следующих `apply` пройдёт, но VCS-шлюз и вход пользователей через GitHub работать не будут:

| Переменная | Откуда |
| --- | --- |
| `github_app_id` | переменная `APP_ID_DMC268_T1` |
| `github_app_private_key` | секрет `APP_PRIVATE_KEY_DMC268_T1` — PEM целиком, с переносами строк |
| `github_app_client_id` | переменная `APP_CLIENT_ID_DMC268_T1` |
| `github_app_client_secret` | секрет `APP_CLIENT_SECRET_DMC268_T1` |
| `public_origin` | `https://` + `VPS_DMC268_IP_T1`: интерфейс отдаётся по HTTPS на тот же адрес. Callback `<public_origin>/api/v1/auth/github/callback` регистрируется в настройках GitHub App |

Из этого стека наружу не смотрит ничего. API, PostgreSQL и RabbitMQ публикуются на петле сервера, воркер не публикуется вовсе; снаружи доступен только интерфейс, и API — через его `/api/`.

## Стек наблюдаемости

Рядом с API на стенде стоит стек телеметрии из семи контейнеров: OTel Collector (`dmc268-otelcol`),
Loki (логи), Tempo (трейсы), Prometheus (метрики), Grafana (интерфейс, `grafana_port` на
`internal_bind_ip`), `node-exporter` (сервер: CPU, память, диски) и `cAdvisor` (контейнеры).
Метрики базы снимает сам коллектор — receiver `postgresql` (решение #89), отдельного экспортёра
Postgres нет. Конфиги лежат в `observability/` и **те же самые** используются локальным
docker compose (профиль `observability`): различия окружений — только переменные и лимиты, не
конфиги. Образы стека зафиксированы дайджестами (`имя@sha256:…`, дайджест манифест-листа) в
`main.tf` и в `docker-compose.yml` — одинаково; тег в комментарии рядом, обновление образа —
осознанная замена дайджеста в обоих файлах (у приёмника `file_log` коллектора операторы
stanza — beta, обновление с чтением breaking changes).

Конфиги заливаются в контейнеры при создании (`upload` в `docker_container`): tofu читает их из
рабочей копии и передаёт через Docker API, поэтому на сервере не нужен чекаут репозитория. Правка
конфига пересоздаёт контейнер следующим `apply`; если провайдер не заметил изменение содержимого,
контейнер пересоздают явно (`tofu apply -replace=docker_container.otelcol`). Локально тот же
стек поднимается профилем: `OTEL_TRACES_EXPORTER=otlp OTEL_METRICS_EXPORTER=otlp docker compose
--profile observability up -d` (Grafana — http://127.0.0.1:3000 без логина).

Экспорт телеметрии API, воркера и миграций включён окружением (`local.telemetry_env`):
`service.version` берётся из тега `api_image` (это sha коммита сборки), окружение задаёт
`deployment_environment_name` — `production`, потому что стенд и есть прод (#97), то же значение
шлёт кабинет (#108). Коллектор читает `json-file`-логи контейнеров из `/var/lib/docker/containers`
(только чтение, от root) и раскладывает по хранилищам; чувствительные данные он редактирует до
отправки — правила в `observability/otelcol.yaml`, тест `tests/infrastructure/test_collector_redaction.py`.

Переменные стека: `observability_memory_mb` (потолки памяти без свопа, семь контейнеров),
`grafana_port`, `grafana_admin_password` (секрет; локально Grafana без логина, на стенде — по
учётке), `postgres_monitor_password` (секрет, см. таблицу выше), `deployment_environment_name`.
Метрики базы коллектор снимает receiver-ом `postgresql`: роль только-чтение `dmc268_monitor` с
`pg_monitor` и расширение `pg_stat_statements` (тяжёлые запросы) создаёт миграция 0011,
`shared_preload_libraries` включает контейнер Postgres. RabbitMQ отдаёт метрики встроенным
плагином `rabbitmq_prometheus` (порт 15692 в сети, per-object эндпоинт — метрики очередей с
лейблом `queue`); сервер — `node-exporter` (9100), контейнеры — `cAdvisor` (8080); все скреятся
`prometheus.yml`. Дашборд «Обзор dmc-268» провиженится из репозитория
(`observability/grafana/provisioning/dashboards/`). Диски ограничены: у Prometheus —
`--storage.tsdb.retention.size=8GB` рядом с потолком по времени, у Loki OSS размерного retention
нет (только `retention_period`) — заполнение диска закрывают алерты #100 по
`node_filesystem_avail_bytes`. Применение стека на стенд — задача DevOps (#108).

Память стека без нагрузки — по замерам ревью PR #130 (Grafana ~350 МБ, Tempo ~200 МБ, Loki
~90 МБ, коллектор ~85 МБ) и локальному прогону Docker Desktop (2026-10-24: node-exporter
~13 МБ, cAdvisor ~45 МБ, коллектор ~65 МБ, Prometheus сразу после старта ~170 МБ и растёт
по мере накопления). Потолки памяти задаются переменной `observability_memory_mb` с запасом
против этих цифр: фактическое потребление — это ориентир при её пересмотре, а не гарантия
(под нагрузкой Loki и Tempo растут).

## Хранилище состояния

Состояние Terraform лежит не рядом с конфигурацией, а в S3-совместимом хранилище (RustFS) на том же сервере (`backend.tf`). Так оно переживает прогон раннера GitHub Actions, который исчезает вместе со своим диском.

Хранилище слушает только `127.0.0.1`, поэтому `tofu` любой командой, которой нужно состояние, работает через SSH-туннель:

```bash
ssh -i <ключ> -o ExitOnForwardFailure=yes -f -N -L 9000:127.0.0.1:9000 <user>@<server>
```

Учётные данные хранилища передаются как `AWS_ACCESS_KEY_ID` и `AWS_SECRET_ACCESS_KEY`; в CI они приходят из секретов `S3_USER_DMC268_T1` и `S3_PASSWORD_DMC268_T1`. Состояния API и UI лежат в одном бакете `dmc268-tfstate` под разными ключами.

Состояние блокируется на время каждой команды, которая его меняет (`use_lockfile`): OpenTofu кладёт рядом lock-объект условной записью, и второй запуск получает `Error acquiring the state lock` с ID блокировки. Если запуск упал, не сняв блокировку, её снимают вручную — только убедившись, что никто больше не работает с состоянием:

```bash
cd infra && tofu force-unlock <ID из ошибки>
```

Сам контейнер хранилища **не входит в этот стек**: его поднимает composite action `.github/actions/s3-backend` первым job'ом деплоя, а не Terraform, иначе `apply` мог бы снести хранилище собственного состояния. Практическое следствие: `tofu destroy` его не удалит, и полная зачистка сервера состоит из двух шагов — `tofu destroy`, а затем `docker rm -f dmc268-s3` и `docker volume rm dmc268-s3-data` вручную.

## Развёртывание и удаление

Штатный путь один: **мерж в `develop`**. Дальше всё делает `deploy.yml` — собирает образ, поднимает хранилище состояния, применяет конфигурацию, проверяет `/health` и что воркер работает. Руками ничего запускать не нужно и, как правило, не получится.

Локальный запуск перестал быть самодостаточным с появлением `backend.tf`: состояние лежит в хранилище на сервере, поэтому любая команда, которой нужно состояние, требует открытого туннеля и учётных данных хранилища:

```bash
ssh -i <ключ> -o ExitOnForwardFailure=yes -f -N -L 9000:127.0.0.1:9000 <user>@<server>
export AWS_ACCESS_KEY_ID=<логин хранилища> AWS_SECRET_ACCESS_KEY=<пароль> AWS_DEFAULT_REGION=us-east-1
cd infra && tofu init && tofu plan
```

Ключ и учётные данные лежат в секретах GitHub и наружу не отдаются, так что у большинства их не будет — и это нормально: чтобы разрабатывать, разворачивать стенд не нужно, достаточно `docker compose up` из корня репозитория.

Проверить конфигурацию без доступа к состоянию можно всегда:

```bash
cd infra && tofu fmt -check && tofu init -backend=false && tofu validate
```

`tofu destroy` удалит стек, но **не** контейнер хранилища состояния — он не входит в этот стек намеренно. Полная зачистка описана ниже, в разделе про хранилище.

Выходы: `api_url`; чувствительные: `postgres_url`, `rabbitmq_url` (пароли в URL кодируются через `urlencode`).

Этот стек только для API. UI деплоится отдельно из `dmc-268-ui-t1/infra/`.
