# Proposal: installation_id в сообщении задачи ревью

## Why

Разбор диффа вынесен к воркеру (design D3): intake не хранит дифф, воркер
повторно достаёт его через `VcsGateway.fetch_diff(repo_full_name, pr_number,
installation_id)`. Сообщение очереди не несёт `installation_id`, а хранить его
в базе негде — таблица `repositories` знает только провайдера и его
идентификатор. Воркер не сможет аутентифицироваться installation-токеном:
Context Level 1 не собирается в сквозной сценарий. Замечание ревью PR #37
(P1, farranfox).

## What Changes

- **BREAKING** (формат сообщения очереди, не REST): тело задачи в RabbitMQ
  дополняется полем `installation_id` — идентификатор инсталляции GitHub App,
  под которым воркер запрашивает дифф и метаданные PR.
- `ReviewJob` получает поле `installation_id: int`; use case приёма передаёт
  его из события вебхука.
- Формат §4.2 (`docs/SYSTEM_DESIGN.md`) пополняется полем; wire-адаптер
  очереди сериализует и разбирает его.

Схема базы данных не меняется. REST-контракт `ReviewJob` (`openapi.yaml`)
не меняется: поле внутреннее, для пути вебхук → брокер → воркер.

## Capabilities

### New Capabilities

- нет

### Modified Capabilities

- `webhook-intake`: требование «Постановка задачи ревью в очередь» — формат
  тела сообщения дополняется `installation_id`; сценарий «Сообщение несёт
  только доменные данные» уточняется.

## Impact

- `app/domain/entities.py` (`ReviewJob`), `app/application/use_cases/handle_webhook.py`,
  `app/infrastructure/queue/rabbitmq.py` (`to_wire_message`/`from_wire_message`),
  `docs/SYSTEM_DESIGN.md` §4.2, спека `openspec/specs/webhook-intake/spec.md`.
- Потребитель сообщения пока один и в этом же PR-цикле: воркер #36 читает
  сообщение тем же `from_wire_message`. Развёрнутый брокер со старым форматом
  несовместим — очередь опустошается деплоем (данные не теряются: источник
  истины — `review_runs` в базе).
