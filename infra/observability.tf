# Стек наблюдаемости (change add-observability-stack): коллектор и его экземпляр
# метрик контейнеров, Loki, Tempo, Prometheus, Grafana, node-exporter и
# socket-proxy — те же конфиги из observability/, что и в локальном compose.
# Метрики базы снимает receiver postgresql самого коллектора (решение #89) —
# отдельного экспортёра нет. Конфиги заливаются в контейнеры при создании
# (upload), поэтому на сервере не нужен чекаут репозитория: tofu читает файлы
# из рабочей копии и передаёт через Docker API. Правка конфига пересоздаёт
# контейнер следующим apply. Алиасы сети повторяют имена сервисов compose —
# одни и те же адреса работают в обоих окружениях (prometheus.yml, grafana
# datasources, endpoint коллектора). Сеть стека internal; публикуемый порт
# Grafana живёт в отдельной не-internal сети dmc268-grafana и открывается
# SSH-туннелем. Выход в интернет через неё есть только у Grafana, и её
# собственные походы наружу (статистика, проверки обновлений Grafana и
# плагинов) выключены env.
# Стек поднимается только при observability_enabled: count = 1 : 0 у образа,
# сети и контейнера; выключение списка телеметрии см. local.telemetry_env.
# Томам с данными count за флагом не нужен: они создаются всегда, поэтому
# откат флагом не строит план, который их уничтожает (и не падает на
# prevent_destroy), — данные телеметрии переживают выключение флага.

resource "docker_image" "otelcol" {
  count        = var.observability_enabled ? 1 : 0
  name         = local.otelcol_image
  keep_locally = true
}

resource "docker_image" "loki" {
  count        = var.observability_enabled ? 1 : 0
  name         = local.loki_image
  keep_locally = true
}

resource "docker_image" "tempo" {
  count        = var.observability_enabled ? 1 : 0
  name         = local.tempo_image
  keep_locally = true
}

resource "docker_image" "prometheus" {
  count        = var.observability_enabled ? 1 : 0
  name         = local.prometheus_image
  keep_locally = true
}

resource "docker_image" "grafana" {
  count        = var.observability_enabled ? 1 : 0
  name         = local.grafana_image
  keep_locally = true
}

resource "docker_image" "node_exporter" {
  count        = var.observability_enabled ? 1 : 0
  name         = local.node_exporter_image
  keep_locally = true
}

resource "docker_image" "docker_socket_proxy" {
  count        = var.observability_enabled ? 1 : 0
  name         = local.docker_socket_proxy_image
  keep_locally = true
}

resource "docker_volume" "loki" {
  name = "dmc268-loki-data"

  # Логи за 30 дней — данные, а не кэш: план, который уничтожает или
  # заменяет том, OpenTofu отклоняет (infra/postgres.tf, «prevent_destroy»).
  # count за флагом у томов нет: с ним откат observability_enabled строил бы
  # план уничтожения тома поверх включённого стенда и падал бы на
  # prevent_destroy («Resource instance cannot be destroyed»), а выключение
  # флага должно снимать только контейнеры (замечание ревью PR #130).
  lifecycle {
    prevent_destroy = true
  }
}

resource "docker_volume" "tempo" {
  name = "dmc268-tempo-data"

  # prevent_destroy и отсутствие count — как у тома Loki выше.
  lifecycle {
    prevent_destroy = true
  }
}

resource "docker_volume" "prometheus" {
  name = "dmc268-prometheus-data"

  # prevent_destroy и отсутствие count — как у тома Loki выше.
  lifecycle {
    prevent_destroy = true
  }
}

resource "docker_volume" "grafana" {
  name = "dmc268-grafana-data"

  # prevent_destroy и отсутствие count — как у тома Loki выше.
  lifecycle {
    prevent_destroy = true
  }
}

# Отдельная сеть для Docker API через socket-proxy: в ней только proxy и
# otelcol_docker (receiver docker_stats) — остальным контейнерам стека и
# приложения доступ к демону Docker не нужен.
resource "docker_network" "observability_docker_proxy" {
  count = var.observability_enabled ? 1 : 0
  name  = "dmc268-docker-proxy"
  # Без выхода наружу: proxy в интернет не ходит.
  internal = true
}

# Сеть стека наблюдаемости — отдельно от обеих сетей приложения: входа
# (dmc268, там интерфейс dmc-268-ui-t1) и данных (backend): без этого из
# контейнеров UI доступны Loki без аутентификации, Prometheus, Tempo и
# приёмник OTLP (замечание ревью PR #130). Internal, как и сеть данных:
# через неё в интернет не выйти, а изоляция от интерфейса достигается самим
# членством в сетях — dmc268 у стека нет вовсе. Выход наружу есть только у
# Grafana — через сеть публикации порта ниже. В backend из стека ходят
# только prometheus (скрейпит rabbitmq) и коллектор (принимает OTLP api и
# воркера, скрейпит postgres и rabbitmq).
resource "docker_network" "observability" {
  count = var.observability_enabled ? 1 : 0
  name  = "dmc268-observability"
  # Изолирована от интернета целиком; публикуемый порт Grafana живёт в
  # отдельной сети dmc268-grafana ниже: порты на internal-сети Docker не
  # публикует (проверено).
  internal = true
}

# Сеть публикации порта Grafana — единственное её назначение: порты на
# internal-сети Docker не публикует, поэтому Grafana стоит и в отдельной
# не-internal сети, и в internal-сети стека. Наружу порт открыт только на
# bind_ip (петля) и через SSH-туннель; в dmc268, сети интерфейса, Grafana не
# подключена.
resource "docker_network" "grafana" {
  count = var.observability_enabled ? 1 : 0
  name  = "dmc268-grafana"
}

resource "docker_container" "otelcol" {
  count   = var.observability_enabled ? 1 : 0
  name    = "dmc268-otelcol"
  image   = docker_image.otelcol[0].image_id
  restart = "unless-stopped"

  memory      = var.observability_memory_mb.collector
  memory_swap = var.observability_memory_mb.collector
  log_driver  = "json-file"
  log_opts    = local.log_opts

  # Капли и запрет новых привилегий — как у api; read_only здесь НЕ ставим:
  # провайдер заливает конфиг upload-ом (CopyToContainer, как docker cp),
  # а демон отказывает в записи в read-only rootfs ещё до пути — apply
  # упал бы на создании контейнера. Локальный коллектор read_only: конфиг
  # приходит read-only маунтом (docker-compose.yml).
  tmpfs         = { "/tmp" = "rw,noexec,nosuid,size=64m" }
  security_opts = ["no-new-privileges:true"]

  capabilities {
    drop = ["ALL"]
  }

  upload {
    content = file("${path.module}/observability/otelcol.yaml")
    file    = "/etc/otelcol-contrib/config.yaml"
  }

  env = [
    # Реквизиты роли мониторинга базы (receiver postgresql в otelcol.yaml):
    # роль создаёт миграция 0013 с тем же паролем (шаг миграций деплоя,
    # .github/scripts/deploy/migrate.sh),
    # имя совпадает с переменной POSTGRES_MONITOR_PASSWORD настроек.
    "POSTGRESQL_USERNAME=dmc268_monitor",
    "POSTGRESQL_PASSWORD=${var.postgres_monitor_password}",
    "POSTGRESQL_ENDPOINT=dmc268-postgres:5432",
    "POSTGRESQL_DATABASE=${var.postgres_db}",
  ]

  # Приёмник OTLP (api и воркер) и цели receiver-ов — в сети данных.
  networks_advanced {
    name = docker_network.backend.name
  }

  # Трейсы браузера: nginx интерфейса проксирует на dmc268-otelcol:4319 —
  # отдельный приёмник otlp/browser, только трейсы (otelcol.yaml).
  networks_advanced {
    name = docker_network.otlp.name
  }

  # Экспортёры (Loki, Tempo) и скрейп Prometheus — в сети стека; алиас —
  # там же: collector:8889 и :8888 скреит Prometheus.
  networks_advanced {
    name    = docker_network.observability[0].name
    aliases = ["collector"]
  }

  depends_on = [
    docker_container.loki,
    docker_container.tempo,
  ]
}

# Метрики контейнеров — отдельный экземпляр того же образа: Docker API через
# socket-proxy, без входящего OTLP и без сетей данных. Почему так —
# infra/README.md, «Метрики контейнеров».
resource "docker_container" "otelcol_docker" {
  count   = var.observability_enabled ? 1 : 0
  name    = "dmc268-otelcol-docker"
  image   = docker_image.otelcol[0].image_id
  restart = "unless-stopped"

  memory      = var.observability_memory_mb.collector_docker
  memory_swap = var.observability_memory_mb.collector_docker
  log_driver  = "json-file"
  log_opts    = local.log_opts

  # Как у основного коллектора: без read_only из-за upload конфига.
  tmpfs         = { "/tmp" = "rw,noexec,nosuid,size=16m" }
  security_opts = ["no-new-privileges:true"]

  capabilities {
    drop = ["ALL"]
  }

  upload {
    content = file("${path.module}/observability/otelcol-docker.yaml")
    file    = "/etc/otelcol-contrib/config.yaml"
  }

  # Скрейп Prometheus: collector-docker:8889 и :8888.
  networks_advanced {
    name    = docker_network.observability[0].name
    aliases = ["collector-docker"]
  }

  # Docker API — только через socket-proxy (dockerproxy:2375).
  networks_advanced {
    name = docker_network.observability_docker_proxy[0].name
  }

  # Receiver docker_stats на старте читает список контейнеров: без proxy этот
  # экземпляр не поднимается.
  depends_on = [docker_container.docker_socket_proxy]
}

resource "docker_container" "loki" {
  count   = var.observability_enabled ? 1 : 0
  name    = "dmc268-loki"
  image   = docker_image.loki[0].image_id
  restart = "unless-stopped"

  memory      = var.observability_memory_mb.loki
  memory_swap = var.observability_memory_mb.loki
  log_driver  = "json-file"
  log_opts    = local.log_opts

  # Хранилища стека заменяются обычной правкой (сторож их не держит, тома те
  # же): 30 с на штатную остановку, чтобы Loki, Tempo и Prometheus сбросили
  # WAL и буферы, а не получили SIGKILL сразу после стоп-сигнала. stop_timeout —
  # окно docker stop, destroy_grace_seconds — то же при пересоздании
  # провайдером. Так же у tempo, prometheus и grafana ниже.
  stop_timeout          = 30
  destroy_grace_seconds = 30

  security_opts = ["no-new-privileges:true"]

  upload {
    content = file("${path.module}/observability/loki.yaml")
    file    = "/etc/loki/config.yaml"
  }

  command = ["-config.file=/etc/loki/config.yaml"]

  volumes {
    volume_name    = docker_volume.loki.name
    container_path = "/loki"
  }

  networks_advanced {
    name    = docker_network.observability[0].name
    aliases = ["loki"]
  }
}

resource "docker_container" "tempo" {
  count   = var.observability_enabled ? 1 : 0
  name    = "dmc268-tempo"
  image   = docker_image.tempo[0].image_id
  restart = "unless-stopped"

  memory      = var.observability_memory_mb.tempo
  memory_swap = var.observability_memory_mb.tempo
  log_driver  = "json-file"
  log_opts    = local.log_opts

  stop_timeout          = 30
  destroy_grace_seconds = 30

  security_opts = ["no-new-privileges:true"]

  upload {
    content = file("${path.module}/observability/tempo.yaml")
    file    = "/etc/tempo/config.yaml"
  }

  command = ["-config.file=/etc/tempo/config.yaml"]

  volumes {
    volume_name    = docker_volume.tempo.name
    container_path = "/var/tempo"
  }

  networks_advanced {
    name    = docker_network.observability[0].name
    aliases = ["tempo"]
  }
}

resource "docker_container" "prometheus" {
  count   = var.observability_enabled ? 1 : 0
  name    = "dmc268-prometheus"
  image   = docker_image.prometheus[0].image_id
  restart = "unless-stopped"

  memory      = var.observability_memory_mb.prometheus
  memory_swap = var.observability_memory_mb.prometheus
  log_driver  = "json-file"
  log_opts    = local.log_opts

  stop_timeout          = 30
  destroy_grace_seconds = 30

  security_opts = ["no-new-privileges:true"]

  upload {
    content = file("${path.module}/observability/prometheus.yml")
    file    = "/etc/prometheus/prometheus.yml"
  }

  command = [
    "--config.file=/etc/prometheus/prometheus.yml",
    # Хранение метрик — 30 дней (как retention логов Loki).
    "--storage.tsdb.retention.time=30d",
    # Потолок по размеру, а не только по времени: всплеск кардинальности
    # не должен забить диск раньше срока хранения. Свободное место видно
    # алертам (#100) по node_filesystem_avail_bytes от node-exporter.
    "--storage.tsdb.retention.size=8GB",
  ]

  volumes {
    volume_name    = docker_volume.prometheus.name
    container_path = "/prometheus"
  }

  # Скрейп rabbitmq — в сети данных; сеть стека — для целей collector,
  # collector-docker, node-exporter и datasources Grafana, алиас — там же.
  networks_advanced {
    name = docker_network.backend.name
  }

  networks_advanced {
    name    = docker_network.observability[0].name
    aliases = ["prometheus"]
  }
}

# Доступ к Grafana — по учётке: анонимный вход выключен, пароль админа из
# переменной (локальный compose, наоборот, поднимает Grafana без логина).
resource "docker_container" "grafana" {
  count   = var.observability_enabled ? 1 : 0
  name    = "dmc268-grafana"
  image   = docker_image.grafana[0].image_id
  restart = "unless-stopped"

  memory      = var.observability_memory_mb.grafana
  memory_swap = var.observability_memory_mb.grafana
  log_driver  = "json-file"
  log_opts    = local.log_opts

  stop_timeout          = 30
  destroy_grace_seconds = 30

  security_opts = ["no-new-privileges:true"]

  upload {
    content = file("${path.module}/observability/grafana/provisioning/datasources/datasources.yaml")
    file    = "/etc/grafana/provisioning/datasources/datasources.yaml"
  }

  # Провижининг дашборда: файл-провайдер и сам дашборд, как у datasources.
  # Каталог сканируется Grafana на *.json, yaml провайдера там же игнорируется.
  upload {
    content = file("${path.module}/observability/grafana/provisioning/dashboards/dashboards.yaml")
    file    = "/etc/grafana/provisioning/dashboards/dashboards.yaml"
  }

  upload {
    content = file("${path.module}/observability/grafana/provisioning/dashboards/dmc268-overview.json")
    file    = "/etc/grafana/provisioning/dashboards/dmc268-overview.json"
  }

  env = [
    "GF_SECURITY_ADMIN_PASSWORD=${var.grafana_admin_password}",
    "GF_AUTH_ANONYMOUS_ENABLED=false",
    "GF_USERS_ALLOW_SIGN_UP=false",
    # Телеметрия вендора off: статистика использования и проверки обновлений
    # Grafana и плагинов не должны уходить с сервера наружу. То же в compose и у Loki/Tempo в их
    # конфигах (loki.yaml, tempo.yaml).
    "GF_ANALYTICS_REPORTING_ENABLED=false",
    "GF_ANALYTICS_CHECK_FOR_UPDATES=false",
    "GF_ANALYTICS_CHECK_FOR_PLUGIN_UPDATES=false",
  ]

  volumes {
    volume_name    = docker_volume.grafana.name
    container_path = "/var/lib/grafana"
  }

  ports {
    internal = 3000
    external = var.grafana_port
    ip       = var.bind_ip
  }

  # Сеть стека — для datasources (loki, tempo, prometheus); алиас зеркалит
  # имя сервиса compose, по нему никто в стеке не ходит — оставлен для
  # симметрии с остальным стеком.
  networks_advanced {
    name    = docker_network.observability[0].name
    aliases = ["grafana"]
  }

  # Публикуемый порт — в отдельной не-internal сети: порты на internal-сети
  # Docker не публикует.
  networks_advanced {
    name = docker_network.grafana[0].name
  }

  depends_on = [
    docker_container.loki,
    docker_container.tempo,
    docker_container.prometheus,
  ]
}

# Метрики сервера (алерты #100): CPU, память, диск, файловые системы хоста.
# Каноничный запуск образа: корень хоста только для чтения (--path.rootfs),
# /proc и /sys — источники статистики ядра. Имя контейнера с префиксом
# dmc268-, алиас сети — как имя сервиса compose.
resource "docker_container" "node_exporter" {
  count   = var.observability_enabled ? 1 : 0
  name    = "dmc268-node-exporter"
  image   = docker_image.node_exporter[0].image_id
  restart = "unless-stopped"

  memory      = var.observability_memory_mb.node_exporter
  memory_swap = var.observability_memory_mb.node_exporter
  log_driver  = "json-file"
  log_opts    = local.log_opts

  security_opts = ["no-new-privileges:true"]

  command = [
    "--path.rootfs=/rootfs",
    "--path.procfs=/host/proc",
    "--path.sysfs=/host/sys",
  ]

  volumes {
    host_path      = "/"
    container_path = "/rootfs"
    read_only      = true
  }

  volumes {
    host_path      = "/proc"
    container_path = "/host/proc"
    read_only      = true
  }

  volumes {
    host_path      = "/sys"
    container_path = "/host/sys"
    read_only      = true
  }

  networks_advanced {
    name    = docker_network.observability[0].name
    aliases = ["node-exporter"]
  }
}

# Прокси над Docker API (/var/run/docker.sock): otelcol_docker — единственный
# клиент, docker.sock контейнерам больше не монтируется. Белый список путей — не
# env-переменные образа (CONTAINERS=1 открывал ЛЮБОЙ GET под /containers —
# /containers/{id}/archive?path=… читал любой файл любого контейнера, включая
# том Postgres, /export отдавал файловую систему, inspect — полный Env;
# замечание ревью PR #130), а свой шаблон haproxy: entrypoint образа рендерит
# файл /usr/local/etc/haproxy/haproxy.cfg.template (sed подставляет адрес
# прослушивания вместо ${BIND_CONFIG}) и стартует haproxy по результату.
# Белый список и остаточный риск — в шапке observability/docker-socket-proxy.cfg.
# Сеть — отдельная, только proxy и otelcol_docker.
resource "docker_container" "docker_socket_proxy" {
  count   = var.observability_enabled ? 1 : 0
  name    = "dmc268-docker-proxy"
  image   = docker_image.docker_socket_proxy[0].image_id
  restart = "unless-stopped"

  memory      = var.observability_memory_mb.docker_proxy
  memory_swap = var.observability_memory_mb.docker_proxy
  log_driver  = "json-file"
  log_opts    = local.log_opts

  # Как у postgres: entrypoint образа сам управляет привилегиями haproxy.
  security_opts = ["no-new-privileges:true"]

  # Шаблон конфига заливается при создании, до старта: entrypoint успевает
  # отрендерить его при запуске. Правка содержимого пересоздаёт контейнер
  # следующим apply — так же, как у коллектора и Grafana выше.
  upload {
    content = file("${path.module}/observability/docker-socket-proxy.cfg")
    file    = "/usr/local/etc/haproxy/haproxy.cfg.template"
  }

  volumes {
    host_path      = "/var/run/docker.sock"
    container_path = "/var/run/docker.sock"
    read_only      = true
  }

  networks_advanced {
    name    = docker_network.observability_docker_proxy[0].name
    aliases = ["dockerproxy"]
  }
}
