# Proposal: add-context-engine-and-repo-rules

## Why

Воркер сегодня отправляет модели заготовку: `assemble_context_stub` пакует в
`ContextPayload` идентификаторы задачи и синтетический hunk `PLACEHOLDER.md`,
настоящего диффа, окружения изменённого кода и правил владельца репозитория
модель не видит. Находки сверяются с тем же синтетическим hunk'ом, поэтому
`validate_anchor` отбрасывает всё, что указывает в реальный код.

Задача «Инженер 3 — Context Engine & Repo Rules Loader» требует собрать
настоящий контекст прогона: дифф, AST-окружение изменённых строк (родительские
классы, сигнатуры методов, импорты) и правила владельца из
`.review/rules.md`. Ревью задачи добавило два требования: жёсткий размер
контекста 32k токенов и защиту от prompt injection — `rules.md` и код
приходят из репозитория, который контролирует автор PR.

## What Changes

- **Загрузка правил репозитория** (`load_repo_rules`, модуль
  `repo_config_loader`): `.review/rules.md` довыбирается через `VcsGateway` на
  `base_sha` прогона — коммите целевой ветки, а не на `head_sha` PR. Ответ
  кэшируется в `CacheStore` по неизменяемому ключу «репозиторий, base_sha».
  Нет файла, сбой VCS, файл больше лимита — дефолтный фоллбэк: ревью идёт по
  стандартным критериям, прогон не падает.
- **Markdown-парсер правил** — чистая функция `parse_rules_markdown`: правило —
  раздел `## <имя>` со строками `pattern:` / `exclude:` и нумерованным списком
  проверок (формат из спеки `ai-review-prompting`). Правило без любой из
  четырёх частей отклоняется с причиной. Проза вне структуры в контекст не
  попадает.
- **AST-окружение изменённых строк**: файлы диффа довыбираются на `head_sha`
  (с лимитами числа файлов и байт), парсер за портом `DefinitionExtractor`
  (адаптер на tree-sitter, грамматики Python, TypeScript/TSX, JavaScript, Go)
  возвращает определения с диапазонами строк и импорты файла; чистая функция
  `select_scopes` оставляет только определения, охватывающие изменённые
  строки, — родительский класс и сигнатуру метода.
- **Единый `ContextPayload`**: чистая функция `assemble_context` собирает тело
  `context-payload/v1` — правила, дифф по файлам, AST-окружение — под жёстким
  лимитом `context_max_tokens` (по умолчанию 32 000). Приоритет при нехватке
  бюджета: правила (в пределах своей доли) → дифф → AST; не поместившееся
  перечисляется в `omitted`, а не обрезается молча.
- **Защита от prompt injection**: правила берутся только из целевой ветки;
  весь текст из репозитория (hunk'и, сигнатуры, импорты, проверки правил)
  оборачивается в блоки `untrusted` с границей, производной от содержимого
  (автор PR не может закрыть блок заранее); управляющие и bidi-символы
  (Trojan Source) делаются видимыми; системный промпт запрещает исполнять
  инструкции из таких блоков.
- **Разбор диффа у воркера**: `parse_diff` и `filter_diff_files` по уже
  принятым требованиям `webhook-intake` (задача #38) — без них нет ни hunk'ов
  для AST, ни честной проверки привязок. Находки сверяются с hunk'ами
  настоящего диффа, заготовка `PLACEHOLDER.md` удаляется.

## Capabilities

### New Capabilities

- `repo-rules`: загрузка `.review/rules.md` из целевой ветки, разбор
  Markdown-правил, кэширование, дефолтный фоллбэк, отбор правил по файлам
  диффа.
- `context-assembly`: сборка `ContextPayload` из диффа, AST-окружения и
  правил; лимит токенов; лимиты довыборки; отделение недоверенных данных;
  парсинг за портом.

### Modified Capabilities

- `review-pipeline`: требование «Собранный контекст сохраняется даже как
  заготовка» заменяется сборкой реального контекста; находки сверяются с
  разобранным диффом прогона.
- `ai-review-prompting`: добавлено требование о недоверенных данных в
  контексте.

## Impact

- **Код**: `app/domain/` (`diff_parser`, `repo_rules`, `code_context`,
  `untrusted`, `context_budget`, новые сущности), `app/application/`
  (`repo_config_loader`, `context_assembly`, порты `CacheStore`,
  `DefinitionExtractor`, метод `VcsGateway.fetch_file`, `run_review`),
  `app/infrastructure/` (in-process кэш, tree-sitter, `fetch_file` в
  GitHub-адаптере, composition root), `app/worker/factory.py`, `app/config.py`.
- **Зависимости**: `tree-sitter`, `tree-sitter-python`,
  `tree-sitter-javascript`, `tree-sitter-typescript`, `tree-sitter-go` через
  `uv`; колёса под cp314 проверяются первой задачей.
- **Промпты**: `prompts/review/system.md`, `prompts/review/custom-rules.md`
  (Markdown-форма правил в `.review/rules.md`).
- **Документы**: `docs/SYSTEM_DESIGN.md` §5 (правила, лимит, недоверенные
  блоки), `docs/BACKEND_ARCHITECTURE.md` (порты, модель угроз).
- **Окружение**: GitHub App нужно разрешение **Contents: read**.
- **База данных**: схема не меняется — `tiers` и `body` уже свободной формы,
  миграций нет.
- **Соседние задачи**: #38 закрывается этим change'ем (парсер и фильтр);
  #40 (`add-tree-sitter-index`) получает готовые `CacheStore`,
  `DefinitionExtractor` и `fetch_file` — см. design.md, D8.
