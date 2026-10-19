# Tasks: add-tree-sitter-index

Порядок — TDD по слоям (`.agents/rules/backend.md`): чистые функции
домена и решения тестируются без базы, брокера и сети; адаптеры — своими
адаптерными тестами. Проверки перед коммитом каждого шага: `uv run ruff
check .`, `uv run lint-imports`, `uv run pytest`.

Порты `CacheStore` и `DefinitionExtractor`, их адаптеры
(`InMemoryCacheStore`, `TreeSitterDefinitionExtractor`) и реестр грамматик уже
в `develop` — задач на их создание change не содержит; отдельного спайка
зависимостей не нужно (tree-sitter используется AST-уровнем).

## 1. Конфигурация

- [ ] 1.1 `uv sync --all-extras`; проверить: тесты ветки зелёные
      (`uv run pytest`).
- [ ] 1.2 Настройки лимитов в `app/config.py`: `index_max_files`,
      `index_max_file_bytes`, `index_timeout_seconds`; проверить: тест в
      `tests/test_config.py` читает их из окружения.

## 2. Домен: выбор записей индекса

- [ ] 2.1 Чистые функции в `app/domain/definitions_index.py`: разрешение
      импортов изменённых файлов в пути репозитория и выбор записей индекса
      «дифф + импорт-окрестность» (записи самих изменённых файлов
      исключаются — их контур на `head_sha` уже в пакете); проверить:
      юнит-тесты на литералах в `tests/domain/test_definitions_index.py` —
      сосед по импорту выбран, нерелевантный файл нет, изменённый файл не
      дублируется, пустой индекс даёт пустой выбор.

## 3. Порты приложения

- [ ] 3.1 Метод `fetch_tree(repo_full_name, sha, installation_id)` —
      рекурсивный список путей файлов дерева коммита — в
      `app/application/ports/vcs_gateway.py` + фейк в `tests/fakes/`;
      проверить: гвард-тест порта без инфраструктурных деталей, обновлённые
      тесты фейка. `fetch_file` не трогаем: существующий контракт (обязательный
      `max_bytes`, `None` при отсутствии файла, `VcsFileTooLargeError` сверх
      лимита) переиспользуется как есть.

## 4. Адаптеры инфраструктуры

- [ ] 4.1 Trees API в GitHub-адаптере `app/infrastructure/vcs/github.py`
      (`GET /repos/{o}/{r}/git/trees/{sha}?recursive=1`); проверить: тесты в
      `tests/infrastructure/test_github_vcs_gateway.py` — тело, backoff
      на 429/5xx, пустое дерево, флаг `truncated` (усечённое дерево —
      частичный список без сбоя).

## 5. Прикладной слой

- [ ] 5.1 Use case `ensure_definitions_index`: ключ «репозиторий, base_sha» →
      попадание: вернуть индекс без работы; промах: `fetch_tree` → фильтр по
      реестру языков → `fetch_file(max_bytes=index_max_file_bytes)` +
      `DefinitionExtractor` → сериализация и запись в `CacheStore` с ttl;
      `VcsFileTooLargeError` и `VcsError` — пропуск файла, исчерпание
      `index_max_files`/`index_timeout_seconds` — частичный индекс, сбой
      построения — признак промаха наверх, не исключение; проверить:
      юнит-тесты сценария на фейках — полный индекс, частичный при исчерпании
      лимитов, файл неизвестного языка, слишком большой файл, повторный вызов
      не парсит, сбой не роняет сценарий.
- [ ] 5.2 Потребление в сборке контекста: `build_review_context` при
      попадании выбирает записи функцией домена (задача 2.1) и передаёт их в
      `assemble_context` вторым источником уровня `ast` — отдельного значения
      в `tiers` не появляется; записи идут в бюджет после AST-блоков
      изменённых файлов, не поместившееся — в `omitted`; промах — контекст без
      словаря; проверить: тесты сборки контекста — сосед по импорту в пакете,
      нерелевантные записи нет, изменённый файл не дублируется, вытеснение в
      `omitted` при нехватке бюджета, промах.

## 6. Встраивание в конвейер ревью

- [ ] 6.1 `run_review` на шаге `BUILDING_CONTEXT` вызывает
      `ensure_definitions_index` и передаёт индекс в сборку контекста; сбой
      индексации не переводит прогон в failed (best-effort); проверить:
      тесты пайплайна и воркера (`tests/application/`,
      `tests/worker/`) — прогон с индексом, прогон при сбое индексации.

## 7. Документы и сквозные проверки

- [ ] 7.1 `docs/SYSTEM_DESIGN.md`: правка принципа «контекст без клона»
      (индекс определений основной ветки разрешён, полный клон запрещён),
      §4.3 — второй потребитель `CacheStore`, §5 — второй источник уровня
      `ast`; проверить: дифф документа согласован с design.md (D1, D7).
- [ ] 7.2 `docs/BACKEND_ARCHITECTURE.md`: `fetch_tree` в таблице портов
      (`VcsGateway`); проверить: дифф документа.
- [ ] 7.3 Сквозные проверки: `uv run ruff check .`, `uv run lint-imports`,
      `uv run mypy .`, `uv run pytest`, `uv run alembic heads` (одна
      ревизия), `npx @fission-ai/openspec@1.14.2 validate --all --strict`;
      синхронизация дельт `definitions-index` и `context-assembly` в
      `openspec/specs/` при архивации change'а.
