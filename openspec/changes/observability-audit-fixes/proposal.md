## Why

Аудит наблюдаемости стенда 30.10 (larchanka-training/dmc-268-api-t1#263): из трейса не открываются его логи, две панели дашборда показывают не то, своих метрик у коллекторов, Loki и Tempo в Prometheus нет, 97% записей API в Loki — access-лог health-check'а, ошибки шагов прохода очистки неразличимы: тело записи с исключением коллектор гасит. Логи PostgreSQL, RabbitMQ и nginx интерфейса есть только в `docker logs` и пропадают при пересоздании контейнера.

## What Changes

- Grafana: переход «трейс → логи» по лейблу Loki `service_name` и `trace_id` из structured metadata.
- Дашборд: соединения приложения с базой — по `db_client_connection_state`, CPU сервера — доля занятости.
- Prometheus скрейпит свои метрики обоих коллекторов (`otelcol_*`), Loki и Tempo.
- Экспортёры коллектора — тип `otlp_http` вместо устаревшего алиаса `otlphttp`.
- Access-запись health-check'а в лог не пишется.
- Ошибка шага прохода очистки воркера несёт поле `maintenance_step`.
- Логи PostgreSQL и RabbitMQ — в Loki: при включённом стеке их пишет драйвер `fluentd` демона Docker в третий экземпляр коллектора `dmc268-otelcol-logs` (приём только на `127.0.0.1:24224` хоста); он скрывает IP-адреса, пароль в тексте SQL и `DETAIL` ошибок PostgreSQL и отдаёт записи основному коллектору по OTLP. Лог-файлы, каталог Docker и Docker API коллекторы по-прежнему не читают: в файлах и в inspect — Env всех контейнеров с секретами. nginx интерфейса подключается тем же драйвером отдельным PR в `dmc-268-ui-t1`.

## Capabilities

### New Capabilities

### Modified Capabilities

- `observability`: переход трейс → логи, свои метрики стека, health-check вне access-лога, поле шага в ошибках прохода очистки, логи контейнеров без SDK через драйвер `fluentd`.

## Impact

- Конфиги `infra/observability/*`: на стенде пересоздаются оба коллектора, Prometheus и Grafana (конфиги заливаются upload-ом).
- Порт 8888 коллекторов на хосте не публикуется: он доступен из сетей, к которым подключён контейнер коллектора.
- `app/infrastructure/logging.py`, `app/worker/factory.py`.
- Смена драйвера логов пересоздаёт `dmc268-postgres` и `dmc268-rabbitmq` (тома сохраняются). Сторож плана такую замену автоматическим деплоем не пропустит: выкат — ручной запуск `gh workflow run deploy.yml --ref develop -f allow_stateful_replace=true`. На время замены (до 60 с на остановку каждого) API и воркер отвечают ошибками базы и брокера; стенд учебный, простой допустим. Пока коллектор логов не поднят полным `apply`, записи копит демон Docker (`fluentd-async`).
- `docker logs` у этих контейнеров работает через кэш dual logging Docker (по умолчанию 5 файлов по 20 МБ), а не через `json-file`.
