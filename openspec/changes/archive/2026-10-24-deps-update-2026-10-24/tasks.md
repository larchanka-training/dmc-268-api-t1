# Tasks

## 1. Python-зависимости

- [x] 1.1 `uv lock --upgrade`
- [x] 1.2 ruff `extend-select = ["DTZ"]`, mypy `num_workers = "auto"`

## 2. Инструменты и образы

- [x] 2.1 uv 0.13.5 в `Dockerfile` и workflow, `setup-uv` v10.3.2
- [x] 2.2 OpenTofu 1.13.2 в CI и деплое, пол `required_version`
- [x] 2.3 RabbitMQ 4.4 во всех описаниях стека, новый том на стенде
- [x] 2.4 Ollama 0.40.10, комментарий о `prompt_eval_count`
- [x] 2.5 RustFS 1.0.3, пересоздание контейнера при смене образа

## 3. Приёмка

- [x] 3.1 Все проверки из `ci.yml`: ruff, lint-imports, mypy, pytest без пропусков на
      PostgreSQL и RabbitMQ 4.4, `export_openapi.py --check`, openspec, `tofu fmt`/`validate`
- [x] 3.2 Скрипт RustFS на локальном Docker: 1.0.0 → 1.0.3 сохраняет объект в томе;
      недоступный образ оставляет контейнер прежним
- [x] 3.3 `tests/test_s3_script.py`: образ скачивается до удаления контейнера, неудачи `pull` и
      `rm` оставляют прежний контейнер и не выдаются за обновление
