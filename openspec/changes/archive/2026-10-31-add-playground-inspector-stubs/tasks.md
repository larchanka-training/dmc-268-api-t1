## 1. Общие модели и коды ошибок

- [x] 1.1 `SimulationStatus` и `DiscardStage` в `app/domain/enums.py`
- [x] 1.2 `RATE_LIMITED`, `SIMULATION_NOT_FOUND`, `FINDING_NOT_FOUND` в `app/api/problems.py` (модели, варианты `AnyProblem`) и словари `ERRORS_429_RATE_LIMITED` (с заголовком `Retry-After`), `ERRORS_404_SIMULATION`, `ERRORS_404_REVIEW_OR_FINDING` в `app/api/errors.py`
- [x] 1.3 `app/api/schemas.py`: `RuleRefResponse` и `FindingBase` из `app/api/reviews.py`, `VerifierVerdictResponse`, `FileContextResponse` с `ShownRuleResponse` и `ContextOmissionResponse`; `FindingResponse` наследует `FindingBase`, его схема не меняется

## 2. Заглушки

- [x] 2.1 `POST /api/v1/rules/parse` в `app/api/playground.py`: `RulesParseRequest`, `RulesParseResponse` (`ParsedRuleResponse`, `ParsedCheckResponse`, `ParsedRejectedRuleResponse`); ответ `501`
- [x] 2.2 `POST /api/v1/simulations` (`202`) и `GET /api/v1/simulations/{simulation_id}` в `app/api/playground.py`: `SimulationRequest` (`CodeSource` или `DiffSource` по `kind`), `SimulationResponse` (`SimulationFindingResponse`, `DiscardedFindingResponse`, `RuleMentionResponse`); ответ `501`
- [x] 2.3 `GET /api/v1/reviews/{review_run_id}/findings/{finding_id}/inspection` в `app/api/inspection.py`: `FindingInspectionResponse`; видимость прогона (`visible_run`) и принадлежность находки прогону до ответа `501`
- [x] 2.4 Роутеры `playground` и `inspection` подключены в `create_app`

## 3. Тесты, контракт и проверки

- [x] 3.1 Тесты: `501` на допустимый запрос, `401` без сессии, `403 ORIGIN_FORBIDDEN` для `POST` без `Origin`, `422` на id и тело, `404` невидимого прогона и чужой находки, схемы и коды ошибок в экспортированном контракте
- [x] 3.2 Экспорт `docs/openapi/openapi.json` (`uv run python scripts/export_openapi.py`)
- [x] 3.3 `ruff check`, `lint-imports`, `mypy`, `pytest`, `export_openapi.py --check`, `openspec validate --all --strict` проходят
