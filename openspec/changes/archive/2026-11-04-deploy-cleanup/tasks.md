## 1. Уборка

- [x] 1.1 Удалить `diagnostics.sh`, шаг в `deploy.yml`, строку в `deploy_report.py`; `docs/GITHUB_APP.md` — запрос для Grafana Explore
- [x] 1.2 Удалить одноразовую уборку после #159 и #203: `docker rm -f dmc268-migrate`, блоки `removed`, абзацы README (в плане последнего деплоя нет `forget`, контейнера на стенде нет)
- [x] 1.3 Удалить шаг «Убедиться, что образ забирается обратно»
- [x] 1.4 `alembic/monitor_password.py` вместо двух heredoc; тесты сверяют вызов и сам файл
- [x] 1.5 Ссылки на `infra/main.tf` → настоящие файлы; `DATABASE_URL` из CI
- [x] 1.6 Удалить `check-api.sh`, его шаг и строку отчёта; правка требований `backend-delivery`, README и комментария в `app.tf`
- [x] 1.7 Панель «Свободное место на дисках» — через запись `dmc268:filesystem_avail:ratio` (на стенде те же `/` и `/boot/efi`)
- [x] 1.8 `pytest`, ruff, mypy, shellcheck, zizmor, `openspec validate --all --strict`
