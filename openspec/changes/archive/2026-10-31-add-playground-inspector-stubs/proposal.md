## Why

Песочница правил (#287) и инспектор замечания (#289) нужны фронтенду (dmc-268-ui-t1#112, #113), а ручек под них нет.
Порядок тот же, что у #113 и #82, - «контракт первым»: ручки заводятся заглушками `501 NOT_IMPLEMENTED` с полными схемами, фронтенд берёт типы через `pnpm api:sync` и работает на моках, реализация - отдельно (#288, #290).

## What Changes

- `POST /api/v1/rules/parse` - заглушка разбора текста `.review/rules.md` без модели: принятые правила с номерами строк заголовка и пунктов, отклонённые разделы с причиной и строкой, файлы из `paths`, к которым правило применимо (`RulesParseResponse`).
  Нужна для подсказок в редакторе песочницы.
- `POST /api/v1/simulations` - заглушка запуска симуляции ревью: вход - фрагмент кода с путём файла (`kind = code`) или unified diff (`kind = diff`) и текст rules.md; ответ `202` с `SimulationResponse`.
  В схеме - `429 RATE_LIMITED` с `Retry-After`.
- `GET /api/v1/simulations/{simulation_id}` - заглушка чтения симуляции: статус (`SimulationStatus`), замечания со ссылкой на правило, уверенностью и вердиктом верификатора, отброшенные отсевом или верификатором с причиной (`DiscardStage`), контекст модели по файлам, срок хранения.
  В схеме - `404 SIMULATION_NOT_FOUND`.
- `GET /api/v1/reviews/{review_run_id}/findings/{finding_id}/inspection` - заглушка данных инспектора: итог второго прохода прогона, вердикт верификатора по находке, контекст модели по файлу находки (`FindingInspectionResponse`).
  Видимость прогона и принадлежность находки прогону проверяются до `501`: `404 REVIEW_NOT_FOUND` или `404 FINDING_NOT_FOUND`.
- Общий поток `GET /api/v1/events` описывает типы `simulation.status_changed` и `simulation.finished` с данными `SimulationSummary`; пока симуляции нет, они не отправляются.
- Новые коды ошибок: `RATE_LIMITED` (`429`), `SIMULATION_NOT_FOUND` (`404`), `FINDING_NOT_FOUND` (`404`).
- Поля находки, общие для прогона и симуляции, вынесены в `FindingBase`; схема `FindingResponse` не меняется.

Вне этого change: сама симуляция (очередь, воркер, хранение, лимиты, отправка событий) - #288; хранение вердикта верификатора и отдача контекста прогона - #290, контекст - после редакции секретов (#28).
Точные значения лимитов частоты и срока хранения задаёт реализация.

## Capabilities

### New Capabilities

### Modified Capabilities

- `review-rest-api`: заглушки ручек песочницы правил и инспектора замечания, типы `simulation.*` в описании общего потока событий.

## Impact

- Код: новые `app/api/playground.py` и `app/api/inspection.py`; `app/api/schemas.py` (`FindingBase`, `RuleRefResponse`, модели вердикта и контекста), `app/api/reviews.py` (`FindingResponse` от `FindingBase`), `app/api/events.py`, `app/api/problems.py`, `app/api/errors.py`, `app/api/factory.py`; `app/domain/enums.py` (`SimulationStatus`, `DiscardStage`).
- Контракт: `docs/openapi/openapi.json` - четыре операции, новые схемы и коды ошибок, два типа событий потока; существующие схемы не меняются.
- Тесты: `tests/test_problem_details.py`, `tests/test_reviews_api.py`.
