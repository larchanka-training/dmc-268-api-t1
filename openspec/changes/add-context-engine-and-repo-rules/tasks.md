# Tasks: add-context-engine-and-repo-rules

Порядок — TDD по слоям (`.agents/rules/backend.md`): чистые функции домена
тестируются на литералах без базы, брокера и сети; адаптеры — своими
адаптерными тестами. Проверки каждого шага: `uv run ruff check .`,
`uv run lint-imports`, `uv run pytest`.

## 1. Зависимости и настройки

- [x] 1.1 Спайк: `uv add tree-sitter tree-sitter-python tree-sitter-javascript
      tree-sitter-typescript tree-sitter-go`; проверить: импорт всех пяти
      модулей и разбор строки каждой грамматикой под Python 3.14, сборка образа.
      Итог: колёса cp314 (ядро) и abi3 (грамматики) есть под macOS arm64 и
      manylinux x86_64/aarch64; сам образ локально не собирался — проверит CI.
- [x] 1.2 Настройки в `app/config.py`: `context_max_tokens` (32000),
      `rules_max_tokens`, `rules_max_bytes`, `rules_cache_ttl_seconds`,
      `ast_max_files`, `ast_max_file_bytes`; проверить: `tests/test_config.py`
      читает их из окружения и отклоняет неположительные.

## 2. Домен: чистые функции

- [x] 2.1 `parse_diff`, `filter_diff_files` (`app/domain/diff_parser.py`,
      восстановление из #37) + сущность `ParsedFile`, поле `Hunk.text`;
      проверить: `tests/domain/test_diff_parser.py` — multi-file, бинарный,
      rename, no-newline, пустой, фильтр шума, текст hunk'а.
- [x] 2.2 `parse_rules_markdown`, `applicable_rules` (`app/domain/repo_rules.py`)
      + сущности `RepoRule`, `RejectedRule`, `RepoRules`; проверить:
      `tests/domain/test_repo_rules.py` — полное правило, отклонения с
      причинами, проза отброшена, glob c `**` и исключением.
- [x] 2.3 `language_for_path`, `touched_lines`, `select_scopes`
      (`app/domain/code_context.py`) + `Language`, `Definition`,
      `FileOutline`; проверить: `tests/domain/test_code_context.py`.
- [x] 2.4 `sanitize`, `fence` (`app/domain/untrusted.py`); проверить:
      `tests/domain/test_untrusted.py` — точная граница, поддельное закрытие,
      bidi и C0 видимы, `\t`/`\n` сохранены.
- [x] 2.5 `estimate_tokens` и бюджет (`app/domain/context_budget.py`);
      проверить: `tests/domain/test_context_budget.py` — аддитивность
      стоимости, отказ сверх бюджета.

## 3. Порты приложения

- [x] 3.1 `CacheStore` (`app/application/ports/cache_store.py`) + фейк;
      проверить: тест фейка.
- [x] 3.2 `DefinitionExtractor` (`app/application/ports/definition_extractor.py`)
      + фейк; проверить: сигнатура только в доменных типах.
- [x] 3.3 `VcsGateway.fetch_file` + фейк; проверить: тесты фейка.

## 4. Адаптеры инфраструктуры

- [x] 4.1 `InMemoryCacheStore` с TTL (`app/infrastructure/cache/memory.py`),
      часы инъекцией; проверить: запись, чтение, истечение TTL.
- [x] 4.2 `TreeSitterDefinitionExtractor` (`app/infrastructure/parsing/`);
      проверить: по фикстуре на каждый язык — классы с базами, методы с
      родителем, функции, импорты, диапазоны строк; синтаксическая ошибка не
      роняет извлечение.
- [x] 4.3 `GitHubVcsGateway.fetch_file` (contents API, raw); проверить:
      `tests/infrastructure/test_github_vcs_gateway.py` — тело, 404 → `None`,
      путь с пробелом экранирован, backoff на 5xx.

## 5. Прикладной слой

- [x] 5.1 `load_repo_rules` (`app/application/repo_config_loader.py`);
      проверить: юнит-тесты на фейках — `loaded`, `absent`, `too_large`,
      `unavailable` не кэшируется, попадание в кэш без VCS, чтение на `base_sha`.
- [x] 5.2 `assemble_context` — чистая сборка тела `context-payload/v1` под
      лимитом (`app/application/context_assembly.py`, заготовка удаляется);
      проверить: состав тела, `tiers`, `file_paths`, лимит на большом диффе,
      `omitted`, приоритет диффа над AST, бюджет правил, недоверенные блоки.
- [x] 5.3 `build_review_context` — I/O-сценарий; проверить: сбой
      `fetch_diff` пробрасывается, сбой файла AST деградирует контекст,
      лимит `ast_max_files`, большой файл без AST, файлы на `head_sha`.

## 6. Встраивание в конвейер

- [x] 6.1 `run_review` вызывает `build_review_context`, сверяет находки с
      hunk'ами диффа; `ReviewHandlerDeps` и composition root отдают
      `vcs_gateway`, `definition_extractor`, `cache_store` (один на процесс);
      проверить: `tests/application/test_review_pipeline.py`,
      `tests/worker/` — прогон с реальным контекстом, сбой диффа → `failed`.

## 7. Промпты, документы, сквозные проверки

- [x] 7.1 `prompts/review/system.md` — правило недоверенных блоков;
      `prompts/review/custom-rules.md` — Markdown-форма `.review/rules.md`,
      чтение из целевой ветки.
- [x] 7.2 `docs/SYSTEM_DESIGN.md` §5, `docs/BACKEND_ARCHITECTURE.md` (порты,
      модель угроз), README — разрешение App **Contents: read**.
- [x] 7.3 `uv run ruff check .`, `uv run lint-imports`, `uv run mypy .`,
      `uv run pytest`, `uv run alembic heads`, `openspec validate --strict`.
      Синхронизация дельт в `openspec/specs/` и архивация — после мерджа.
