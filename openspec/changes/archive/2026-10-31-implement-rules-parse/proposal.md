## Why

Редактору `.review/rules.md` в песочнице правил (dmc-268-ui-t1#112) нужны подсказки сразу при вводе: что станет правилом, что отклонено и почему, к каким файлам правило применимо.
Ручка `POST /api/v1/rules/parse` уже в контракте заглушкой `501` (change `add-playground-inspector-stubs`); это первый шаг реализации песочницы (#288), он не зависит ни от модели, ни от симуляции.

## What Changes

- `POST /api/v1/rules/parse` отвечает `200` вместо `501`: разбор текста тем же парсером, что ревью применяет к `.review/rules.md`, без вызова модели.
- Парсер правил знает строки: `outline_rules_markdown` отдаёт принятые правила со строкой заголовка и строками пунктов и отклонённые разделы со строкой заголовка; `parse_rules_markdown`, которым пользуется ревью, - тот же разбор без позиций.
  Строки нумеруются с 1 и делятся только по `\n`, как в git и в редакторе.
- `matched_paths` - файлы из `paths`, к которым правило применимо; тем же `matching_files` отбирает файлы правила сборка контекста ревью.
- `rules` больше лимита, с которым ревью читает `.review/rules.md` (`RULES_MAX_BYTES`, в байтах UTF-8), - `422` в `body.rules`: такой файл ревью не разбирает вовсе, и песочница не должна обещать обратного.
  Лимит берётся из конфигурации, поэтому проверку делает ручка, а не модель тела; `ApiError` для этого научился нести `errors`.

## Capabilities

### New Capabilities

### Modified Capabilities

- `review-rest-api`: разбор правил песочницы реализован - требования заглушки заменены требованиями ответа и проверки тела.

## Impact

- Код: `app/domain/repo_rules.py` (`outline_rules_markdown`, `RuleOutline`, `RejectedOutline`, `matching_files`), `app/api/playground.py`, `app/api/errors.py` (`ApiError.errors`).
- Контракт: `docs/openapi/openapi.json` - у `POST /rules/parse` нет `501`, описание операции; схемы не меняются.
- Тесты: `tests/domain/test_repo_rules.py`, новый `tests/test_playground_api.py`, `tests/test_problem_details.py`.
- Ревью запросов на изменение не меняется: `parse_rules_markdown` отдаёт те же правила в том же порядке, это проверяет тест.
