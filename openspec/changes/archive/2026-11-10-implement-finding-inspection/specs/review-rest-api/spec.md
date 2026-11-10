## RENAMED Requirements

- FROM: `### Requirement: Инспекция находки описана заглушкой`
- TO: `### Requirement: Инспекция находки`

## MODIFIED Requirements

### Requirement: Инспекция находки

`GET /api/v1/reviews/{review_run_id}/findings/{finding_id}/inspection` SHALL отвечать `200` с `FindingInspectionResponse`: итог верификации прогона, вердикт верификатора и контекст модели по файлу находки.
Ручка SHALL требовать сессию и проверять видимость прогона и принадлежность ему находки, как `GET /api/v1/reviews/{review_run_id}`.
Контекст SHALL отдаваться с секретами, заменёнными меткой, без обращения к VCS.
`verdict` и `context` SHALL допускать `null`.

#### Scenario: Находка видимого прогона

- **WHEN** вызывается инспекция находки видимого прогона, у которой сохранён вердикт `keep` с причиной, а пакет контекста с телом есть
- **THEN** ответ - `200`: `finding_id` находки, `verification` прогона, `verdict` с `keep` и причиной, `context` файла находки с hunk'ами, правилами и AST-окружением

#### Scenario: Вердикт drop у находки безопасности

- **WHEN** находка безопасности `high` осталась среди находок, потому что верификатор не вправе её отбросить, и у неё сохранён вердикт `drop`
- **THEN** `verdict` в ответе - `drop` с причиной верификатора

#### Scenario: Верификатор не отрабатывал

- **WHEN** итог верификации прогона - `unavailable` или `not_required`
- **THEN** `verdict` - `null`, `verification` называет итог прогона

#### Scenario: Тело контекста стёрто по сроку

- **WHEN** тело пакета контекста прогона стёрто по сроку хранения
- **THEN** `context` - `null`, `verdict` по-прежнему отдаётся

#### Scenario: Секрет в контексте

- **WHEN** в сохранённом пакете контекста на месте токена стоит метка
- **THEN** `context` отдаёт метку, а не токен

#### Scenario: Находка другого прогона

- **WHEN** `finding_id` не принадлежит прогону `review_run_id`
- **THEN** ответ - `404` с `code = FINDING_NOT_FOUND`

#### Scenario: Невидимый прогон

- **WHEN** прогона `review_run_id` нет или он не виден пользователю
- **THEN** ответ - `404` с `code = REVIEW_NOT_FOUND`, находка не проверяется

#### Scenario: Неверный id находки

- **WHEN** в пути `finding_id` не UUID
- **THEN** ответ - `422` с ошибкой в `path.finding_id`

#### Scenario: Отказы инспекции в контракте

- **WHEN** фронтенд читает операцию инспекции в `docs/openapi/openapi.json`
- **THEN** ответ `404` допускает `REVIEW_NOT_FOUND` и `FINDING_NOT_FOUND`, `501` в ответах нет, а `FindingInspectionResponse` описывает итог второго прохода прогона, вердикт верификатора по находке с причиной и контекст модели по файлу находки, и `verdict` и `context` допускают `null`
