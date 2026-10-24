# Proposal

## Why

Python-зависимости, инструменты CI и образы стека отстают на минорные версии. Два обновления
закрывают реальные проблемы: uv 0.13.4–0.13.5 и OpenTofu 1.13.2 отклоняют сертификаты
скомпрометированного УЦ eMudhra (GHSA-hq85-5cpj-mr62). Одно обновление без правки тихо ослабляет
проверки: ruff 0.17 убрал из набора по умолчанию правила `DTZ` о наивном `datetime`.

Отдельно — хранилище состояния OpenTofu на стенде: скрипт поднятия RustFS не трогает запущенный
контейнер, поэтому новый тег образа в `deploy.yml` на сервер не попадает никогда.

## What Changes

- `uv lock --upgrade`: fastapi 0.146.0 → 0.162.0, sqlalchemy 2.1.1 → 2.1.6, alembic 1.20.0 →
  1.20.1, pydantic-settings 2.15.0 → 2.16.1, ruff 0.16.9 → 0.17.2, mypy 2.3.1 → 2.4.0,
  pre-commit 4.6.2 → 4.7.0 и транзитивные. `grimp<3.16` и `import-linter<2.15` остаются: причина
  пина в `pyproject.toml` не снята.
- ruff: `extend-select = ["DTZ"]` возвращает правила, выпавшие из набора по умолчанию в 0.17.0.
- mypy: `num_workers = "auto"` — параллельная проверка вышла из experimental в 2.4.
- uv 0.12.21 → 0.13.5 в `Dockerfile` и workflow, `astral-sh/setup-uv` v10.2.0 → v10.3.2.
- OpenTofu 1.12.6 → 1.13.2 в CI и деплое, пол `required_version` — `>= 1.13.0`.
- RustFS 1.0.0 → 1.0.3. Скрипт поднятия хранилища сравнивает образ контейнера с заданным и
  при расхождении пересоздаёт контейнер на том же томе; не скачался образ — остаётся старый.
- RabbitMQ 4.3 → 4.4 во всех описаниях стека. На стенде — новый том: 4.4 пишет хранилище
  classic-очередей в новом формате, и в брокере нет данных, которые стоило бы переносить.
- Ollama 0.12.6 → 0.40.10 в локальном профиле; комментарий о `prompt_eval_count` в адаптере
  приведён к новому поведению.

Python 3.15 не входит: у `tree-sitter` нет колёс cp315, у `grimp` нет cp315 под macOS.

## Capabilities

### New Capabilities

Нет.

### Modified Capabilities

- `backend-delivery`: хранилище состояния развёртывания обновляется до образа из описания
  развёртывания, не теряя состояния.

## Impact

- `uv.lock`, `pyproject.toml`, `Dockerfile`, `docker-compose.yml`, `.github/workflows/*.yml`,
  `.github/scripts/render-s3-script.sh`, `infra/main.tf`, `infra/versions.tf`, `infra/README.md`,
  `app/infrastructure/llm/ollama.py`.
- Стенд: при первом деплое пересоздаются контейнер RustFS (том `dmc268-s3-data` сохраняется) и
  RabbitMQ на новом томе; старый том брокера удаляется.
