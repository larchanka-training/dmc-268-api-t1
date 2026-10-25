# Приложение: образ API из реестра и два контейнера из него — api и worker.
# Миграции сюда не входят: их применяет шаг деплоя до `tofu apply`
# (.github/scripts/deploy/migrate.sh) с окружением из выхода migrate_env.

resource "docker_image" "api" {
  # Образ собирается и публикуется в CI, здесь он только забирается — по
  # digest, который вернула сборка (`api_image` без него не пройдёт проверку).
  # Раньше он собирался прямо отсюда по хешу исходников; при удалённом
  # docker_host такая сборка ушла бы на сервер — без контекста и без кеша.
  name         = var.api_image
  keep_locally = true
}

# Тот же образ под воркер, отдельным ресурсом. Замена ресурса образа ждёт
# уничтожения всех контейнеров, которые на него ссылаются: с общим ресурсом
# новый API не стартовал, пока воркер дорабатывал сообщение (до
# destroy_grace_seconds). Замер на Docker 29.9.2 с воркером, который дренирует
# 60 с: простой API при замене образа 64 с с общим ресурсом, 2,5 с — со своим
# (#159). Порядок уничтожения OpenTofu берёт из состояния, поэтому выигрыш —
# со второго деплоя после перехода. Два ресурса с одним name провайдеру не мешают:
# оба только забирают образ, а keep_locally не даёт удалению одного снять
# образ из-под другого.
resource "docker_image" "worker" {
  name         = var.api_image
  keep_locally = true
}

# Контейнер миграций жил в стеке до #159. Из состояния он уходит без
# уничтожения: остановленный контейнер на сервере убирает шаг миграций.
removed {
  from = docker_container.migrate

  lifecycle {
    destroy = false
  }
}

resource "docker_container" "api" {
  name    = "dmc268-api"
  image   = docker_image.api.image_id
  restart = "unless-stopped"

  # Без destroy_grace_seconds провайдер при пересоздании удаляет контейнер
  # принудительно (SIGKILL): lifespan не завершается, спаны не выгружаются.
  # С ним — сначала SIGTERM и ожидание остановки; 10 с — как у `docker stop`.
  # Укладываемся благодаря `--timeout-graceful-shutdown 5` в CMD образа: открытые
  # потоки SSE uvicorn рвёт через 5 с, остальное время — на lifespan.
  destroy_grace_seconds = 10

  # Наш образ: пользователь не root (USER в Dockerfile), приложение ничего не
  # пишет на диск, поэтому файловая система только для чтения и без capabilities.
  # Те же ограничения у миграций — флаги docker run в migrate.sh.
  read_only     = true
  tmpfs         = { "/tmp" = "rw,noexec,nosuid,size=64m" }
  security_opts = ["no-new-privileges:true"]
  init          = true
  memory        = 512
  memory_swap   = 512
  log_driver    = "json-file"
  log_opts      = local.log_opts

  capabilities {
    drop = ["ALL"]
  }

  # Миграции к этому моменту уже применены шагом деплоя.
  depends_on = [
    docker_container.postgres,
    docker_container.rabbitmq,
  ]

  # API — единственный, кто стоит в обеих сетях: запросы приходят из сети
  # входа, база и брокер — в сети данных. Сеть входа не internal: через неё
  # же API ходит в GitHub (OAuth, метаданные PR), и на ней опубликован порт
  # для проверки живости через туннель.
  networks_advanced {
    name = docker_network.dmc268.name
  }

  networks_advanced {
    name = docker_network.backend.name
  }

  # Реквизиты GitHub App: VCS-шлюз у вебхуков (метаданные PR) и у воркера,
  # OAuth и origin — у входа и проверки сессии, ключ токенов пользователя — у
  # входа и ручек репозиториев.
  env = concat(local.app_env, [
    "GITHUB_APP_ID=${var.github_app_id}",
    "GITHUB_APP_PRIVATE_KEY=${var.github_app_private_key}",
    "GITHUB_APP_CLIENT_ID=${var.github_app_client_id}",
    "GITHUB_APP_CLIENT_SECRET=${var.github_app_client_secret}",
    "PUBLIC_ORIGIN=${var.public_origin}",
    # Ключ шифрования токенов пользователя GitHub — только API: воркер их не читает.
    "USER_TOKEN_ENCRYPTION_KEY=${var.user_token_encryption_key}",
  ])

  ports {
    internal = 8000
    external = var.api_port
    ip       = var.bind_ip
  }

  # Apply считает API выкаченным, только когда тот healthy: провайдер после
  # создания контейнера опрашивает его состояние и роняет apply, если healthy
  # не наступил за wait_timeout. Ждёт он только при создании, поэтому
  # повторный деплой без замены API не замедляет. Здоровый API становится
  # healthy за секунды (start_interval ниже); 90 с — запас медленному старту.
  wait         = true
  wait_timeout = 90

  # /health существует именно для этого. Без healthcheck Docker знает только,
  # что процесс не завершился, и зависший uvicorn остался бы живым в его
  # представлении до следующего деплоя. Значения — в local.api_healthcheck.
  healthcheck {
    test           = local.api_healthcheck.test
    interval       = local.api_healthcheck.interval
    timeout        = local.api_healthcheck.timeout
    retries        = local.api_healthcheck.retries
    start_period   = local.api_healthcheck.start_period
    start_interval = local.api_healthcheck.start_interval
  }

  # Правку healthcheck провайдер 4.6.0 планирует обновлением на месте, но
  # Docker не меняет healthcheck запущенного контейнера: diff остаётся вечным,
  # а проверка — старой (проверено на Docker 29.9.2). Поэтому любая правка
  # healthcheck пересоздаёт API.
  lifecycle {
    replace_triggered_by = [terraform_data.api_healthcheck.output]
  }
}

# Healthcheck API отдельным значением: от него пересоздаётся контейнер API
# (replace_triggered_by), потому что на месте провайдер его не меняет.
#
# start_interval — частота проверок в start_period: без него первая проверка
# шла бы через interval, и wait ждал бы 30 с даже здоровый API. Длительности
# — в формате Go, в котором провайдер читает их обратно ("2s", "30s", "1m0s"):
# "1m" давал бы вечный diff. curl в образе нет, python есть.
locals {
  api_healthcheck = {
    test = [
      "CMD-SHELL",
      "python -c \"import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2).status == 200 else 1)\"",
    ]
    interval       = "30s"
    timeout        = "5s"
    retries        = 3
    start_period   = "10s"
    start_interval = "2s"
  }
}

resource "terraform_data" "api_healthcheck" {
  input = local.api_healthcheck
}

# Воркер: тот же образ, что у API (свой ресурс docker_image.worker), другая команда. Портов нет: с остальным
# стеком он говорит только через очередь и базу (`backend-delivery`), наружу —
# исходящими запросами к GitHub API за диффом, правилами и файлами.
resource "docker_container" "worker" {
  name    = "dmc268-worker"
  image   = docker_image.worker.image_id
  restart = "unless-stopped"
  command = ["python", "-m", "app.worker"]

  # По SIGTERM воркер дорабатывает сообщение в работе. Худший случай — два
  # вызова модели (ревью и верификатор) × LLM_TIMEOUT_SECONDS (300) ×
  # LLM_MAX_ATTEMPTS (2) × провайдеры (два с фоллбэком), плюс минута на GitHub
  # и базу. stop_timeout — для docker stop, destroy_grace_seconds — для
  # пересоздания: без него провайдер удаляет контейнер сразу SIGKILL.
  stop_timeout          = local.worker_stop_seconds
  destroy_grace_seconds = local.worker_stop_seconds

  # Те же основания, что у api: образ один.
  read_only     = true
  tmpfs         = { "/tmp" = "rw,noexec,nosuid,size=64m" }
  security_opts = ["no-new-privileges:true"]
  init          = true
  memory        = 512
  memory_swap   = 512
  log_driver    = "json-file"
  log_opts      = local.log_opts

  capabilities {
    drop = ["ALL"]
  }

  # Как у API: воркер пишет в ту же схему, а миграции уже применены шагом
  # деплоя. Готовности брокера depends_on не ждёт; если воркер поднялся
  # раньше, он упадёт на подключении и рестарт поднимет его снова.
  depends_on = [
    docker_container.postgres,
    docker_container.rabbitmq,
  ]

  networks_advanced {
    name = docker_network.backend.name
  }

  networks_advanced {
    name = docker_network.egress.name
  }

  # Секрет вебхука воркеру не нужен, но без него не загрузятся общие
  # настройки; убрать его можно, когда настройки API и воркера разделятся.
  # Реквизиты App нужны: сборка контекста довыбирает дифф, `.review/rules.md`
  # и изменённые файлы через VCS-шлюз.
  # Адаптер модели: без LLM_BACKEND воркер не стартует, и деплой падает на
  # проверке устойчивой работы. Eurorouter подключается только вторым и только
  # флагом llm_hosted_fallback: тогда код проверяемых репозиториев уходит
  # hosted-провайдеру (docs/BACKEND_ARCHITECTURE.md, модель угроз).
  env = concat(local.app_env, [
    "GITHUB_APP_ID=${var.github_app_id}",
    "GITHUB_APP_PRIVATE_KEY=${var.github_app_private_key}",
    "LLM_BACKEND=${var.llm_backend}",
    "OLLAMA_BASE_URL=${var.ollama_base_url}",
    "OLLAMA_MODEL=${var.ollama_model}",
    "LLM_HOSTED_FALLBACK=${var.llm_hosted_fallback}",
    "EUROROUTER_BASE_URL=${var.eurorouter_base_url}",
    "EUROROUTER_MODEL=${var.eurorouter_model}",
    "EUROROUTER_API_KEYS=${var.eurorouter_api_keys}",
  ])
}
