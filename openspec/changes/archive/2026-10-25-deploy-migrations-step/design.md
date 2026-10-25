# Design

## Миграции шагом деплоя

`docker run --rm` образа выката на демоне стенда (`DOCKER_HOST=ssh://stand`), в сети `dmc268`. Ограничения — те же, что у `api` и `worker`: `--read-only`, `--tmpfs /tmp`, `--cap-drop ALL`, `no-new-privileges`, `--init`, 256 МБ без свопа; их сверяет `tests/test_stack_descriptions.py`. Имя `dmc268-migrations`: второй запуск поверх зависшего первого падает на конфликте имён, а не мигрирует параллельно; `dmc268-migrate` остаётся за старым контейнером.

## Окружение — из одного описания

`output "migrate_env"` — `join("\n", local.app_env)`, sensitive. `migrate.sh` пишет `tofu output -raw` в `mktemp` под `umask 077` и передаёт `--env-file`; файл удаляется в `trap EXIT`. Пароль в DSN закодирован тем же `urlencode`, что у контейнеров: адрес в bash второй раз не собирается.

Targeted apply не записывает выход, который не зависит от целевых ресурсов (проверено на OpenTofu 1.13.2: `Output "…" not found` после `apply -target`, а зависимый выход обновляется и при `No changes`). Поэтому хосты в `local.app_env` — `docker_container.postgres.name` и `docker_container.rabbitmq.name`, а не строки: выход зависит от базы и брокера и обновляется шагом `-target` с переменными текущего прогона.

## Порядок на чистом сервере

`tofu apply -target=docker_network.dmc268 -target=docker_container.postgres -target=docker_container.rabbitmq` на каждом деплое, а не ручной bootstrap: на живом стенде это `No changes`, на чистом — база и брокер до миграций, и тот же шаг обновляет `migrate_env`. Предупреждение OpenTofu о `-target` ожидаемое и вреда не несёт: полный apply идёт следом в том же job'е.

У postgres `wait = true`: провайдер 4.6.0 ждёт `healthy` только после создания контейнера (`resourceDockerContainerCreate`), поэтому на живом стенде атрибут добавляется обновлением на месте без перезапуска (проверено: `StartedAt` не изменился). Цикла повторов в миграциях нет — настоящая ошибка видна с первой попытки. Остаётся окно на пустом томе: `pg_isready` через сокет отвечает и временному серверу инициализации (замер: 0,2 с), но первая проверка идёт через `interval` 10 с, а инициализация занимает меньше секунды. Закрыть окно совсем — `pg_isready -h 127.0.0.1`, вместе с `destroy_grace_seconds` у postgres (#102).

## Свой образ у воркера

`docker_image.worker` с `name = var.api_image`. Провайдер 4.6.0: id ресурса — `<image id><name>`, `Delete` при `keep_locally = true` образ не удаляет, поэтому два ресурса с одним name не конфликтуют, и уничтожение одного не снимает образ из-под другого. Замер (Docker 29.9.2, воркер не выходит по SIGTERM, грейс 60 с): простой API 64,4 с с общим ресурсом; 61,9 с на первом деплое после перехода (порядок из состояния); 2,5 с на втором.

## Учётные данные реестра

`docker/login-action` на раннере, `GITHUB_TOKEN` с `packages: read`. При `docker pull` CLI кладёт учётные данные в `X-Registry-Auth` запроса к удалённому демону — проверено на реестре с паролем: без входа `no basic auth credentials`, после входа образ забирается. На сервере токен не сохраняется.
