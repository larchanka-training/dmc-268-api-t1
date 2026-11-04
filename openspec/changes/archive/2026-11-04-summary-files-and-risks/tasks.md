## 1. Охват и сводка

- [x] 1.1 Тесты домена: группы охвата, блок рисков, `<details>`, враждебный путь, предел 50 (красные до правки)
- [x] 1.2 `review_coverage` и `ReviewCoverage` (design D1)
- [x] 1.3 Риски и список файлов в `format_summary_comment` (design D2–D4)
- [x] 1.4 `AssembledContext.coverage`, параметр `coverage` у `publish_review`, передача из пайплайна; тесты стыков

## 2. Завершение

- [x] 2.1 `ruff`, `lint-imports`, `mypy`, `pytest`, `export_openapi --check`, `openspec validate --all --strict`
- [x] 2.2 Синхронизация спек и `openspec archive summary-files-and-risks`
