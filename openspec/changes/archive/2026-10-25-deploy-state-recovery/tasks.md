# Tasks

## 1. Восстановление состояния

- [x] 1.1 `.github/scripts/s3-tunnel.sh`: закрыть прежний туннель и сторожа, открыть туннель (`ControlPath=none`, `ServerAliveCountMax=12`, `ExitOnForwardFailure`), запустить сторожа; `s3-backend` зовёт его вместо своего шага
- [x] 1.2 `.github/scripts/s3-watchdog.sh`: таймаут запроса к хранилищу дольше `S3_WATCHDOG_LIMIT` — закрыть туннель; отказ в соединении не трогать
- [x] 1.3 `.github/scripts/deploy/recover-state.sh`: туннель, ожидание хранилища, блокировка из `<key>.tflock` (только своя по `Who`), `state push` без `-force` под ней, `force-unlock`; итог — `state-recovery`, сбой — `::error::` с инструкцией
- [x] 1.4 `deploy.yml`: шаг `recover` после упавшего targeted apply, плана или apply; `-lock-timeout=2m` у targeted apply, `plan.sh`, `apply.sh`

## 2. Сверка вершины

- [x] 2.1 `.github/scripts/deploy/check-tip.sh`: `gh api …/commits/develop` против `SHA` из gate; выход `deploy`, файл `stale-tip`
- [x] 2.2 `deploy.yml`: шаг `tip` после checkout, `if: *fresh` у всех шагов выката

## 3. Summary

- [x] 3.1 `scripts/deploy_report.py`: шаги `tip` и `recover` в `STEPS`; `state_section` — чистая функция над `state-recovery` и `s3-watchdog.log`; заголовок «пропущен» по `stale-tip`

## 4. Проверки

- [x] 4.1 `tests/test_deploy_recovery.py`: `recover-state.sh`, сторож, `check-tip.sh` — настоящим bash с подложными `tofu`, `aws`, `curl`, `gh`; раскладка шагов и условий в `deploy.yml`
- [x] 4.2 `tests/test_deploy_report.py`: состав и порядок шагов, summary пропуска и восстановления
- [x] 4.3 Репетиция на `docker:29-dind`, OpenTofu 1.13.2, RustFS 1.0.3: `docker stop` хранилища посреди apply, `docker pause`, повторный деплой при занятой блокировке
- [x] 4.4 `infra/README.md`: «Деплой», «Хранилище состояния», «Если деплой упал посреди apply»

## 5. Замечания ревью

- [x] 5.1 `recover-state.sh`: снимать мёртвую блокировку другого раннера (`runner@…`) и свою после обрыва шага (`INTERRUPTED`); у блокировки apply — `::warning::` и предупреждение в summary
- [x] 5.2 `deploy.yml`: восстановление и после отмены или таймаута (`!success()`, исход `cancelled`), `timeout-minutes: 10`; `s3-tunnel.sh`: `ConnectTimeout=15`, порт после `pkill` — до 15 с с явной ошибкой
- [x] 5.3 Зашифрованная копия `errored.tfstate` (`openssl enc`, отдельный ключ `STATE_BACKUP_KEY_DMC268_T1`) и шаг `state_artifact` с `retention-days: 1`
- [x] 5.4 Сверка serial и lineage с `tofu state pull` перед записью; текст про serial в скрипте и README
- [x] 5.5 Явная ошибка, если в адресе хранилища нет порта; тест на неснятую блокировку (`UNLOCK_EXIT=1`)
- [x] 5.6 `concurrency` группы `deploy` — `queue: max`
