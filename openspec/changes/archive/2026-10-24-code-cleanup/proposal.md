# Proposal

## Why

В коде остались неиспользуемые куски (`anchor_matches_llm`, `_line_for_side`, параметр
`blocking=`, `RepositoryRepo.list_all`), несколько копий выражения «строка на стороне
привязки», docstring портов `JobQueue` и `LlmGateway`, которые выдают их за порты вне
`ports.__all__`, и дерево файлов в плане спринта с удалёнными схемами.

CI ставит зависимости с `--frozen` и не сверяет `uv.lock` с `pyproject.toml`. `TestClient`
на `httpx` выдаёт `StarletteDeprecationWarning`. Тест ухода клиента из потока событий не
проверяет, что подписка на изменения прогонов закрыта.

## What Changes

- Удаляются `anchor_matches_llm`, `_line_for_side`, параметр `blocking=` вместе с
  `BLOCKING_SEVERITIES`, `RepositoryRepo.list_all` из порта, адаптера и фейка и два теста,
  которые проверяли только его.
- У `DiffAnchor` появляется свойство `line`; копии выражения в `publish_review.py`,
  `prompt.py` и `diff.py` используют его. Копия в `review_comment.py` уходит вместе с
  `anchor_matches_llm`.
- Docstring портов и дерево в плане спринта приводятся к действительности.
- `httpx2` — в dev-зависимостях через `uv`; тест про логи принимает и имя логгера `httpx2`.
- В job `lint` в `ci.yml` — шаг `uv lock --check`.
- Тест ухода клиента из потока событий проверяет, что подписка закрыта.

**Не входит:** `find_by_digest` (решено в #28), `record_comment_feedback` / `record_feedback`
(решение команды в #169).

## Capabilities

### New Capabilities

Нет.

### Modified Capabilities

Нет.

`skip_specs: true`: наблюдаемое поведение сервиса не меняется — ни ручки, ни публикация, ни
решение о блокировке мерджа. Меняется только код, тесты, документация и CI.

## Impact

- `app/domain`, `app/application`, `app/infrastructure`: удаление и сведение дублей, без
  изменения результата.
- `pyproject.toml`, `uv.lock`: `httpx2` в extra `dev`.
- `.github/workflows/ci.yml`: устаревший `uv.lock` роняет job `lint`.
