# Tasks: add-tree-sitter-index

Порядок — TDD по слоям (`.agents/rules/backend.md`): чистые функции
домена и решения тестируются без базы, брокера и сети; адаптеры — своими
адаптерными тестами. Проверки перед коммитом каждого шага: `uv run ruff
check .`, `uv run lint-imports`, `uv run pytest`.

## 1. Спайк зависимостей

- [ ] 1.1 `uv sync --all-extras`; проверить: тесты ветки зелёные
      (`uv run pytest`).
- [ ] 1.2 Спайк tree-sitter: `uv add tree-sitter tree-sitter-python`,
      зафиксировать версии в `pyproject.toml` с комментарием о колёсах cp314;
      проверить: `uv run python -c "import tree_sitter, tree_sitter_python"`
      выполняется, сборка образа проходит. При провале — остановка change
      (остальные задачи не начинаются).
- [ ] 1.3 Чистые настройки лимитов в `app/config.py`:
      `index_max_files`, `index_max_file_bytes`, `index_timeout_seconds`;
      проверить: тест в `tests/test_config.py` читает их из окружения.

## 2. Домен: типы и чистые функции

- [ ] 2.1 Чистый тип `Definition` (вид, имя, сигнатура, путь, строка) в
      `app/domain/entities.py`; проверить: юнит-тест в
      `tests/domain/test_entities.py`.
- [ ] 2.2 Чистая функция `language_for_path(path)` — реестр
      «расширение → грамматика» в `app/domain/definitions_index.py`,
      неизвестное расширение даёт `None`; проверить: юнит-тест в
      `tests/domain/test_definitions_index.py`.

## 3. Порты приложения

- [ ] 3.1 Порт `CacheStore` (best-effort get/set с ttl) в
      `app/application/ports/cache_store.py` и фейк в `tests/fakes/`;
      проверить: гвард-тест сигнатуры без вендорных типов и тест фейка.
- [ ] 3.2 Порт `DefinitionExtractor` (исходный код и язык → кортеж
      `Definition`) в `app/application/ports/definition_extractor.py`;
      проверить: гвард-тест — параметры и возврат только доменных типов.
- [ ] 3.3 Методы `fetch_tree(repo_full_name, sha, installation_id)` и
      `fetch_file(repo_full_name, path, sha, installation_id)` в
      `app/application/ports/vcs_gateway.py` + фейк; проверить: обновлённые
      тесты фейка, гвард-тест порта без инфраструктурных деталей.

## 4. Адаптеры инфраструктуры

- [ ] 4.1 In-process адаптер `CacheStore` с ttl в
      `app/infrastructure/cache/`; проверить: адаптерный тест — запись,
      чтение, вытеснение по ttl.
- [ ] 4.2 Адаптер `DefinitionExtractor` на tree-sitter с грамматикой Python
      в `app/infrastructure/parsing/`; проверить: адаптерный тест на
      фикстуре `tests/fixtures/sample.py` — классы и функции с сигнатурами
      и позициями; файл с синтаксической ошибкой не роняет извлечение.
- [ ] 4.3 Методы trees/blobs в GitHub-адаптере
      `app/infrastructure/vcs/github.py` (`GET /repos/{o}/{r}/git/trees/
      {sha}?recursive=1`, blob по пути); проверить: тесты в
      `tests/infrastructure/test_github_vcs_gateway.py` — тело, backoff
      на 429/5xx, пустое дерево.

## 5. Прикладной слой

- [ ] 5.1 Use case `ensure_definitions_index`: ключ «репозиторий,
      base_sha» → попадание: вернуть индекс без работы; промах: `fetch_tree`
      → лимиты (число файлов, размер, время) → `fetch_file` +
      `DefinitionExtractor` → запись в `CacheStore`; сбой довыборки или
      парсинга — признак промаха наверх, не исключение; проверить:
      юнит-тесты сценария на фейках — полный индекс, частичный при исчерпании
      лимитов, файл неизвестного языка, повторный вызов не парсит, сбой не
      роняет сценарий.
- [ ] 5.2 Потребление в сборке контекста: контекст прогона включает словарь
      сигнатур по ключу «репозиторий, base_sha», `tiers` дополняется
      уровнем `ast`; промах — контекст собирается без словаря; проверить:
      тесты сборки контекста — попадание, промах, лимит токенов на словарь.

## 6. Встраивание в конвейер ревью

- [ ] 6.1 `run_review` на шаге `BUILDING_CONTEXT` вызывает
      `ensure_definitions_index` и передаёт индекс в сборку контекста; сбой
      индексации не переводит прогон в failed (best-effort); проверить:
      тесты пайплайна и воркера (`tests/application/`,
      `tests/worker/`) — прогон с индексом, прогон при сбое индексации.

## 7. Документы и сквозные проверки

- [ ] 7.1 `docs/SYSTEM_DESIGN.md`: правка принципа «контекст без клона»
      (индекс определений основной ветки разрешён, полный клон запрещён),
      AST-уровень §5 — потребитель индекса; проверить: дифф документа
      согласован с design.md (D1).
- [ ] 7.2 `docs/BACKEND_ARCHITECTURE.md`: порт `DefinitionExtractor`,
      шов Strangler Fig для выноса парсинга; проверить: дифф документа.
- [ ] 7.3 Сквозные проверки: `uv run ruff check .`, `uv run lint-imports`,
      `uv run mypy .`, `uv run pytest`, `uv run alembic heads` (одна
      ревизия), `openspec validate --strict`; синхронизация дельты
      `definitions-index` в `openspec/specs/` при архивации change'а.
