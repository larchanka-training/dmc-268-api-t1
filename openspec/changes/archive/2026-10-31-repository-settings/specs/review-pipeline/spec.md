## RENAMED Requirements

- FROM: `### Requirement: Merge gate по критичным открытым тредам`
- TO: `### Requirement: Merge gate по открытым тредам не ниже порога`

## MODIFIED Requirements

### Requirement: Merge gate по открытым тредам не ниже порога

До публикации находок система SHALL сверить треды бота с GitHub одним запросом с пагинацией на прогон и SHALL выставить check run `dmc-268-ai-review` на `head_sha` с `conclusion=failure`, если остаётся незакрытый тред с замечанием не ниже порога `min_severity` репозитория. Сверка и check run — best-effort: сбой хостинга SHALL логироваться и SHALL NOT переводить прогон в `failed`. Check run SHALL выставляться по базе после публикации ревью; при сбое сверки находки SHALL публиковаться без неё.

#### Scenario: Ручное закрытие треда снимает блокировку

- **WHEN** тред с critical был закрыт на GitHub вручную до merge gate
- **THEN** `thread_resolved` обновляется при сверке и check run получает `success`

#### Scenario: GraphQL недоступен после публикации

- **WHEN** ревью с замечанием `critical` опубликовано, а на следующем прогоне GraphQL GitHub на сверке тредов отвечает 502 и после повторов адаптера
- **THEN** прогон завершается `completed`, находка опубликована, check run выставлен с `failure` по треду из базы, и сообщение не уходит в DLQ

#### Scenario: Check run не выставился

- **WHEN** ревью опубликовано, а создание check run завершилось сбоем хостинга
- **THEN** прогон завершается `completed`, в лог пишется warning с id прогона, второго ревью нет

#### Scenario: Порог ниже critical блокирует high

- **WHEN** у репозитория `min_severity = high`, и после публикации остаётся незакрытый тред с замечанием `high`
- **THEN** check run — `failure`, в заголовке и тексте назван порог `high`

#### Scenario: Замечание ниже порога не блокирует

- **WHEN** у репозитория `min_severity = critical` (по умолчанию), и открыт только тред с замечанием `high`
- **THEN** check run — `success`

#### Scenario: Пересчёт по вебхуку закрытия треда — по текущему порогу

- **WHEN** порог репозитория сменили с `critical` на `medium`, а затем человек закрыл один из тредов, и остаётся открытый тред `medium`
- **THEN** пересчёт по вебхуку `pull_request_review_thread` ставит `failure`

#### Scenario: Смена порога сама не пересчитывает проверку

- **WHEN** `PUT …/settings` меняет `min_severity`, а новых прогонов и закрытий тредов нет
- **THEN** уже стоящий check run не меняется до следующего прогона или вебхука закрытия треда
