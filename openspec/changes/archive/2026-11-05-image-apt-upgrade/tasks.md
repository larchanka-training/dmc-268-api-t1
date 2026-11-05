# Tasks

## 1. Образ

- [x] 1.1 Рантайм-этап `Dockerfile`: `apt-get install --only-upgrade apt`, списки пакетов удалены в том же слое, `# hadolint ignore=DL3008` с обоснованием

## 2. Приёмка

- [x] 2.1 hadolint 2.12.0 — без замечаний; в собранном образе `apt` и `libapt-pkg7.0` 3.0.4, `/var/lib/apt/lists` пуст
- [x] 2.2 `trivy image --severity HIGH,CRITICAL --ignore-unfixed` — 0 находок; `import app.api.factory, app.worker` в образе проходит
