## Why

Схемы ответа модели (`app/application/llm_schema.py`, `app/infrastructure/llm/structured.py`) — граница, через которую в прогон попадает недоверенный текст модели. DoD спринта 2 (#275) требует для них 100% строк и ветвей, и CI должен это держать, а не ревью. Покрытие при этом не видит двух вещей: модуля, выпавшего из `--cov` (coverage лишь предупреждает `module-not-imported`), и ограничений полей pydantic — они исполняются при импорте и считаются покрытыми всегда.

## What Changes

- Цель `make cov-llm-schema`: тесты двух модулей, порог 100% строк и ветвей, считанный только их собственными тестами; `CoverageWarning` — ошибка. Её вызывают шаг CI в job `lint` и `make check`; README и `.agents/rules/backend.md` ссылаются на цель.
- Тест полноты отказов: обходит поля `ReviewOutput` и `VerificationOutput` с вложенными моделями и требует для каждого поля и каждого `extra="forbid"` случай отказа.
- `.coverage*` в `.gitignore`: оборванный прогон оставляет `.coverage.<host>.pid…`.

## Impact

- `Makefile`, `.github/workflows/ci.yml`, `tests/application/test_llm_schema.py`, `tests/infrastructure/test_llm_structured.py`, `pyproject.toml` (`pytest-cov` в extras `dev`), README, правила бэкенда.
- Впервые CI вызывает `make`: на `ubuntu-latest` он есть.
- Образ собирается с `--no-dev`, `pytest-cov` в него не попадает.
