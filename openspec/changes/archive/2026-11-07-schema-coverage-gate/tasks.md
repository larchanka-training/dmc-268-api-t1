## 1. Порог покрытия

- [x] 1.1 `pytest-cov` в extras `dev`, `uv.lock`
- [x] 1.2 Тесты на непокрытые ветви `llm_schema.py` и `structured.py` до 100% строк и ветвей
- [x] 1.3 Цель `make cov-llm-schema` с `-W error::coverage.exceptions.CoverageWarning`, вызов из `make check` и шага CI
- [x] 1.4 Тест полноты: случай отказа для каждого поля и `extra="forbid"` у `ReviewOutput` и `VerificationOutput`
- [x] 1.5 `.coverage*` в `.gitignore`; README и `.agents/rules/backend.md` ссылаются на цель make
