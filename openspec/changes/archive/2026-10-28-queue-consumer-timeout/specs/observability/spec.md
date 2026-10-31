## MODIFIED Requirements

### Requirement: Спаны очереди — по semantic conventions messaging

Имена и атрибуты спанов публикации и обработки SHALL следовать semantic conventions messaging для RabbitMQ: имя — `{messaging.operation.name} {destination}`, атрибуты `messaging.system`, `messaging.operation.name`, `messaging.operation.type`, `messaging.destination.name` и `messaging.rabbitmq.destination.routing_key`, у обработки — ещё `messaging.rabbitmq.message.delivery_tag`.

#### Scenario: Публикация и обработка задачи

- **WHEN** API публикует задачу в `review_jobs.qq` (default exchange, routing key — имя очереди), а воркер её обрабатывает
- **THEN** спан публикации — `publish review_jobs.qq`, `PRODUCER`, `messaging.operation.type` — `send`; спан обработки — `process review_jobs.qq`, `CONSUMER`, `messaging.operation.type` — `process`, с delivery tag сообщения; у обоих `messaging.system` — `rabbitmq`, `messaging.destination.name` и routing key — `review_jobs.qq`

### Requirement: Описание статуса спана не повторяет текст исключения

Спан, закрытый с ошибкой из-за исключения, SHALL нести в описании статуса только имя типа исключения. Текст исключения SHALL оставаться только в событии `exception`: описание статуса редакция коллектора не проходит, а в тексте бывает ответ провайдера модели или значения ключей из ошибки базы.

#### Scenario: Сбой обработки сообщения

- **WHEN** обработка сообщения падает с `LlmError("Eurorouter: HTTP 400: <фрагмент промпта>")`
- **THEN** спан `process review_jobs.qq` закрыт со статусом ошибки и описанием `LlmError`, а текст есть только в событии `exception`

#### Scenario: Пойманный сбой шага прохода

- **WHEN** проход очистки ловит исключение одного шага и продолжает следующий
- **THEN** спан `review.sweep` отмечен ошибкой с описанием — именем типа, событие `exception` на месте
