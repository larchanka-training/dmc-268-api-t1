# Proposal

## Why

`.github/scripts/s3-tunnel.sh` считал туннель к хранилищу состояния готовым, когда на петле раннера открыт порт. Этот порт слушает сам ssh-клиент, поэтому проверка проходит и тогда, когда хранилище `dmc268-s3` на сервере лежит: ошибка всплывает позже, на `tofu init`, без подсказки. Во фронте это место уже исправлено (larchanka-training/dmc-268-ui-t1#74). Закрывает #211.

## What Changes

- Готовность туннеля — любой ответ HTTP хранилища (`curl -w '%{http_code}'`, всё, кроме `000`), в пределах `S3_TUNNEL_WAIT` (30 с). Не ответило — шаг падает с ошибкой, которая называет контейнер `dmc268-s3`, сторож туннеля не запускается.
- `recover-state.sh` ждал хранилище своим циклом до 120 с уже после открытия туннеля; теперь туннель ждал бы меньше и сдался раньше. Поэтому ожидание одно — в туннеле: `recover-state.sh` передаёт `S3_TUNNEL_WAIT=$RECOVER_WAIT` (120 с), свой цикл удалён.
- Тесты в `tests/test_deploy_recovery.py`: туннель ждёт ответа хранилища; хранилище молчит — шаг падает.

## Capabilities

### New Capabilities

Нет.

### Modified Capabilities

Нет.

`skip_specs: true`: требования `backend-delivery` к туннелю не меняются — меняется признак его готовности.

## Impact

- `.github/scripts/s3-tunnel.sh`, `.github/scripts/deploy/recover-state.sh`, `tests/test_deploy_recovery.py`.
