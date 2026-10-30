## Why

Аудит наблюдаемости стенда 30.10 (larchanka-training/dmc-268-api-t1#263): из трейса не открываются его логи, две панели дашборда показывают не то, своих метрик у коллекторов, Loki и Tempo в Prometheus нет, 97% записей API в Loki — access-лог health-check'а, а ошибки шагов прохода очистки неразличимы: тело записи с исключением коллектор гасит.

## What Changes

- Grafana: переход «трейс → логи» по лейблу Loki `service_name` и `trace_id` из structured metadata.
- Дашборд: соединения приложения с базой — по `db_client_connection_state`, CPU сервера — доля занятости.
- Prometheus скрейпит свои метрики обоих коллекторов (`otelcol_*`), Loki и Tempo.
- Экспортёры коллектора — тип `otlp_http` вместо устаревшего алиаса `otlphttp`.
- Access-запись health-check'а в лог не пишется.
- Ошибка шага прохода очистки воркера несёт поле `maintenance_step`.

Логи контейнеров интерфейса, RabbitMQ и PostgreSQL в Loki в этот change не входят: спека запрещает коллектору читать лог-файлы контейнеров, а безопасного способа без пересоздания базы и брокера и без доступа к файлам всех контейнеров нет.

## Capabilities

### New Capabilities

### Modified Capabilities

- `observability`: переход трейс → логи, свои метрики стека, health-check вне access-лога, поле шага в ошибках прохода очистки.

## Impact

- Конфиги `infra/observability/*`: на стенде пересоздаются оба коллектора, Prometheus и Grafana (конфиги заливаются upload-ом).
- Порт 8888 коллекторов на хосте не публикуется: он доступен из сетей, к которым подключён контейнер коллектора.
- `app/infrastructure/logging.py`, `app/worker/factory.py`.
