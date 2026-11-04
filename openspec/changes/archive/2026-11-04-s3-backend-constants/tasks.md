# Tasks

## 1. Action

- [x] 1.1 Константы вместо inputs `container`, `volume`, `port`, `image`, `cred_user_var`, `cred_pass_var`, `bucket`; в `deploy.yml` остаются `s3_user` и `s3_password`
- [x] 1.2 Удалить шаг «Проверить SSH-подключение»
- [x] 1.3 Шаг «Поднять S3-хранилище» с фейковым `ssh`: на stdin `RUSTFS_ACCESS_KEY` и `RUSTFS_SECRET_KEY`, в скрипте `dmc268-s3`, `dmc268-s3-data`, `9000`, `rustfs/rustfs:1.0.4`

## 2. RustFS 1.0.4

- [x] 2.1 Образ `rustfs/rustfs:1.0.4`
- [x] 2.2 В podman: 1.0.3 с бакетом, версионированием и двумя версиями объекта, затем 1.0.4 на том же томе: объект читается, версионирование `Enabled`, версий две
