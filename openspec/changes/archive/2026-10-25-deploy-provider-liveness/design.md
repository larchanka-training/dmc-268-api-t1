# Design

## Что делает провайдер

kreuzwerker/docker 4.6.0, `resourceDockerContainerCreate`: после `ContainerStart` при `wait = true` раз в секунду читает `State.Health.Status` и возвращает ошибку `container failed to be in healthy state`, если `healthy` не наступил за `wait_timeout`. Ждёт только при создании: `wait` — не ForceNew, на живом контейнере это обновление на месте без перезапуска, и повторный деплой без замены API не замедляется.

Проверено на Docker 29.9.2 (локальный демон в контейнере):

- создание API → `healthy` за 3 с (один раз 11 с: первая проверка упала, healthy — на следующей);
- образ, у которого `/health` не отвечает: apply падает через 94 с; контейнер в состоянии помечен tainted, следующий apply с исправным образом его пересоздаёт; повторный plan — `No changes`;
- `interval = "1m"`: apply проходит, а plan навсегда показывает `"1m0s" -> "1m"` — провайдер читает длительность как `time.Duration.String()`.

## Пересоздание от healthcheck

Блок `healthcheck` в схеме ForceNew, но его вложенные атрибуты — нет: добавление `start_interval` провайдер планирует обновлением на месте, Docker healthcheck запущенного контейнера не меняет, и diff остаётся вечным (проверено). Поэтому описание — `local.api_healthcheck`, его копия — `terraform_data.api_healthcheck.input`, а у API `replace_triggered_by = [terraform_data.api_healthcheck.output]`: любая правка healthcheck пересоздаёт контейнер. Создание `terraform_data` на первом деплое тоже пересоздаёт API — это и нужно, чтобы `start_interval` дошёл до Docker.

## Туннельная проверка

`check-api.sh` остаётся: провайдер видит healthcheck изнутри контейнера, а не публикацию порта на петле и туннель. Ожидание сокращено до 10 попыток раз в секунду: процесс уже `healthy`, а сломанный путь снаружи ожидание не чинит.

## Воркер

Healthcheck и `wait` у воркера — после heartbeat-файла из #157: без него проверять нечего, кроме «процесс жив», а это уже делает `check-worker.sh`.
