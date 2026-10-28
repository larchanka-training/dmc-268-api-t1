# Spec Delta

## ADDED Requirements

### Requirement: Прогон отдаёт режим, покрытие и причины

`ReviewJob` и строка списка прогонов SHALL отдавать режим прогона (`review_mode`), коммит
водяного знака на начало (`since_sha`), покрытие (`coverage`) с причиной (`coverage_reason`),
причину отмены (`cancel_reason`), id вытеснившего прогона (`superseded_by`) и причину отсрочки
(`defer_reason`). Поле без значения SHALL приходить как `null`; существующие поля не меняются.

#### Scenario: Инкрементальный прогон

- **WHEN** запрошен прогон, завершённый в режиме `incremental` с полным покрытием
- **THEN** `review_mode` — `incremental`, `since_sha` — водяной знак на начало прогона, `coverage` — `complete`, `coverage_reason` — `null`

#### Scenario: Вытесненный прогон

- **WHEN** запрошен прогон, отменённый новым push
- **THEN** `status` — `cancelled`, `cancel_reason` — `superseded`, `superseded_by` — id прогона нового head

### Requirement: Находка отдаёт состояние замечания

Находка в `GET /api/v1/reviews/{review_run_id}` SHALL отдавать внутреннее состояние замечания
(`lifecycle_state`, `null` у находки без своего треда), уровень совпадения с прежней находкой
(`match_level`) и id прежнего замечания при повторном появлении (`reappeared_from`).
Fingerprint SHALL NOT отдаваться: это внутренний ключ.

#### Scenario: Возможно исправленное замечание

- **WHEN** запрошен прогон с опубликованной находкой, которую следующий прогон перевёл в `possibly_fixed`
- **THEN** у находки `lifecycle_state` — `possibly_fixed`

### Requirement: Покрытие прогона по файлам отдаётся отдельно

`GET /api/v1/reviews/{review_run_id}/coverage` SHALL отдавать покрытие прогона по файлам: путь,
статус покрытия и число изменённых строк. До реализации ручка SHALL быть заглушкой с полной
схемой, отвечающей `501 NOT_IMPLEMENTED`, и требовать сессию.

#### Scenario: Заглушка покрытия

- **WHEN** пользователь с сессией запрашивает покрытие прогона до реализации ручки
- **THEN** ответ — `501` Problem Details с кодом `NOT_IMPLEMENTED`
