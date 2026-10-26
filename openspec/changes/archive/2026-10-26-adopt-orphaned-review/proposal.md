## Why

Chaos-сьют задачи #222 нашёл дубль замечания в PR. Воркер умирает после того, как GitHub записал ревью прогона, но до записи `published_comments`. Повторная доставка по спеке переводит прогон в `failed` (захват одним воркером), а следующий прогон того же PR (push, перезапуск) публикует замечание к той же строке второй раз: отсев по треду бота (`skip-findings-with-open-thread`) видит только треды из `published_comments`, а комментарии «осиротевшего» ревью туда не попали. То же даёт выметатель зависших прогонов, если прогон умер на публикации.

## What Changes

- Перед публикацией прогон находит прошлые прогоны своего PR, упавшие на этапе `publishing` без единой записи в `published_comments`, ищет на GitHub ревью бота с их маркером `<!-- dmc-268-review-run:<id> -->` и записывает его сводку и inline-комментарии в `published_comments` упавшего прогона. Дальше отсев по треду и merge gate работают как с обычным прошлым ревью.
- Поиск - best-effort: сбой GitHub пишется в лог и публикацию не останавливает.
- Порт `VcsGateway`: метод `find_published_review` - ревью прогона по маркеру с сопоставлением inline-комментариев.
- Хранилище прогонов: `list_unpublished_failures(merge_request_id)`.

## Capabilities

### Modified Capabilities

- `review-pipeline`: ADDED «Ревью упавшего прогона не дублируется следующим».

## Impact

- Код: `app/application/publish_review.py`, порт `app/application/ports/vcs_gateway.py`, `app/application/ports/repositories.py`, адаптеры `app/infrastructure/vcs/github.py`, `github_review.py`, `app/infrastructure/db/repositories.py`.
- Тесты: chaos `tests/chaos/test_worker_crash_chaos.py`, прикладной слой, адаптер GitHub, репозиторий; фейки `RecordingVcsGateway`, `FakeUnitOfWork`.
- Схема базы, контракт API и настройки не меняются. Разрешений App не прибавляется: `GET /pulls/{n}/reviews` уже читается для поиска по маркеру.
