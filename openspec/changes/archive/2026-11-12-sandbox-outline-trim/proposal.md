# Proposal

## Why

Песочница tree-sitter (#236) сама выводила статусы файлов отчёта — `whole_file`, `budget`,
`no_scopes` и другие — по условиям, скопированным из `_collect_asts` и `assemble_context`, и ещё
раз звала `select_scopes`. Поменяйся `build_review_context` — песочница продолжила бы уверенно
печатать старые статусы, а её тесты проверяли только её собственную копию логики. Вокруг ядра
выросла обвязка: doctor на bash и Python, флаги, не влияющие на сборку, тесты на точный текст
отчёта. Замечания ревью #236 вынесены в #253 — этот change их закрывает.

## What Changes

- Отчёт по файлу — только из ответа этапа: элементы блока `ast` пакета (текст как видит модель,
  с обёрткой), записи `omitted` файла и группа охвата `coverage`. Нет ни того ни другого — «AST не
  выбирался»; урезанный бюджетом перечень `omitted` отчёт называет, а не догадывается о причине.
- Удалены `_RecordingExtractor`, `_RecordingVcs`, `_unfence`, `_render_outline`, `OutlineResult`;
  `--full-outline` — один вызов публичного `extract` на тексте файла.
- Дифф для `--lines` и `lines:` строится сразу по номерам строк: затронутые строки — `+`, вокруг —
  контекст, база — файл без этих строк; заглушек `<песочница: строка N>` больше нет.
- Кейс YAML читается один раз: `case_from_raw` в корпусе eval, `case_input` в песочнице.
- Удалены `scripts/sandbox-outline-doctor.sh`, `tests/sandbox/outline/grammars.py`, флаги
  `--window` и `--payload`; `make sandbox-outline-demo` — два шага, подсказка про код 139 осталась.
- В мок-PR добавлен файл больше 8 КБ, у которого в пакете есть блок `ast`.
- Тесты — только ловящие поведение; README песочницы переписан под новый отчёт.

## Capabilities

### New Capabilities

Нет.

### Modified Capabilities

Нет.

`skip_specs: true`: наблюдаемое поведение сервиса не меняется — ни ручки, ни прогон ревью, ни
публикация. Меняются инструмент разработчика в `tests/`, цель `Makefile` и документация.

## Impact

- `tests/sandbox/outline/`: `runner.py`, `sources.py`, `__main__.py`, `test_outline.py`,
  `README.md`, `examples/outline-case.yaml`; удалён `grammars.py`.
- `tests/sandbox/inputs.py`, `tests/sandbox/test_inputs.py`, `tests/eval/rules/corpus.py`.
- `scripts/sandbox-outline-doctor.sh` — удалён.
- `Makefile`: цель `sandbox-outline-demo`.
