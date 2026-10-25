# Стек наблюдаемости (change add-observability-stack): коллектор, Loki, Tempo,
# Prometheus, Grafana, node-exporter, cAdvisor и socket-proxy — те же конфиги
# из observability/, что и в локальном compose. Метрики базы снимает receiver
# postgresql самого коллектора (решение #89) — отдельного экспортёра нет.
# Конфиги заливаются в контейнеры при создании
# (upload), поэтому на сервере не нужен чекаут репозитория: tofu читает файлы
# из рабочей копии и передаёт через Docker API. Правка конфига пересоздаёт
# контейнер следующим apply. Алиасы сети повторяют имена сервисов compose —
# одни и те же адреса работают в обоих окружениях (prometheus.yml, grafana
# datasources, endpoint коллектора). Наружу из стека не смотрит ничего:
# Grafana публикуется на internal_bind_ip и открывается SSH-туннелем.
# Стек поднимается только при observability_enabled: count = 1 : 0 у каждого
# его ресурса — образа, тома, сети и контейнера; выключение списка телеметрии
# см. local.telemetry_env.

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

resource "docker_image" "cadvisor" {
  count        = var.observability_enabled ? 1 : 0
  name         = local.cadvisor_image
  keep_locally = true
}

resource "docker_image" "docker_socket_proxy" {
  count        = var.observability_enabled ? 1 : 0
  name         = local.docker_socket_proxy_image
  keep_locally = true
}

resource "docker_volume" "loki" {
  count = var.observability_enabled ? 1 : 0
  name  = "dmc268-loki-data"
}

resource "docker_volume" "tempo" {
  count = var.observability_enabled ? 1 : 0
  name  = "dmc268-tempo-data"
}

resource "docker_volume" "prometheus" {
  count = var.observability_enabled ? 1 : 0
  name  = "dmc268-prometheus-data"
}

resource "docker_volume" "grafana" {
  count = var.observability_enabled ? 1 : 0
  name  = "dmc268-grafana-data"
}

# Отдельная сеть для Docker API через socket-proxy: в ней только proxy и
# cAdvisor — остальным контейнерам стека и приложения доступ к демону Docker
# не нужен. cAdvisor — единственный, кто ходит и в эту сеть (к proxy), и в
# dmc268 (оттуда его скреит Prometheus).
resource "docker_network" "observability_docker_proxy" {
  count = var.observability_enabled ? 1 : 0
  name  = "dmc268-docker-proxy"
  # Без выхода наружу: proxy и cAdvisor в интернет не ходят.
  internal = true
}

# Сеть стека наблюдаемости — отдельно от сети приложения (dmc268), к которой
# подключается интерфейс (dmc-268-ui-t1): без этого из контейнеров UI доступны
# Loki без аутентификации, Prometheus, Tempo и приёмник OTLP (замечание ревью
# PR #130). internal: контейнерам стека наружу идти не нужно. В dmc268 из
# стека остаются только prometheus (скрейпит rabbitmq) и коллектор (принимает
# OTLP и скрейпит postgres/rabbitmq).
resource "docker_network" "observability" {
  count = var.observability_enabled ? 1 : 0
  name  = "dmc268-observability"
  # Без internal: публикуемый порт Grafana на internal-сети не работает
  # (проверено), а изоляция от интерфейса достигается самим членством в
  # сети — dmc268 у стека нет, кроме коллектора и Prometheus.
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
    # роль создаёт миграция 0013 с тем же паролем (docker_container.migrate),
    # имя совпадает с переменной POSTGRES_MONITOR_PASSWORD настроек.
    "POSTGRESQL_USERNAME=dmc268_monitor",
    "POSTGRESQL_PASSWORD=${var.postgres_monitor_password}",
    "POSTGRESQL_ENDPOINT=dmc268-postgres:5432",
    "POSTGRESQL_DATABASE=${var.postgres_db}",
  ]

  networks_advanced {
    name    = docker_network.dmc268.name
    aliases = ["collector"]
  }

  # Экспортёры (Loki, Tempo) и скрейп Prometheus — в сети стека.
  networks_advanced {
    name = docker_network.observability[0].name
  }

  depends_on = [
    docker_container.loki,
    docker_container.tempo,
  ]
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

  security_opts = ["no-new-privileges:true"]

  upload {
    content = file("${path.module}/observability/loki.yaml")
    file    = "/etc/loki/config.yaml"
  }

  command = ["-config.file=/etc/loki/config.yaml"]

  volumes {
    volume_name    = docker_volume.loki[0].name
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

  security_opts = ["no-new-privileges:true"]

  upload {
    content = file("${path.module}/observability/tempo.yaml")
    file    = "/etc/tempo/config.yaml"
  }

  command = ["-config.file=/etc/tempo/config.yaml"]

  volumes {
    volume_name    = docker_volume.tempo[0].name
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
    volume_name    = docker_volume.prometheus[0].name
    container_path = "/prometheus"
  }

  networks_advanced {
    name    = docker_network.dmc268.name
    aliases = ["prometheus"]
  }

  # Цели в сети стека: collector, node-exporter, cadvisor.
  networks_advanced {
    name = docker_network.observability[0].name
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
    # Телеметрия вендора off: статистика использования и проверка обновлений
    # не должны уходить с сервера наружу. То же в compose и у Loki/Tempo в их
    # конфигах (loki.yaml, tempo.yaml).
    "GF_ANALYTICS_REPORTING_ENABLED=false",
    "GF_ANALYTICS_CHECK_FOR_UPDATES=false",
  ]

  volumes {
    volume_name    = docker_volume.grafana[0].name
    container_path = "/var/lib/grafana"
  }

  ports {
    internal = 3000
    external = var.grafana_port
    ip       = var.internal_bind_ip
  }

  networks_advanced {
    name    = docker_network.observability[0].name
    aliases = ["grafana"]
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

# Метрики контейнеров: CPU и память каждого, включая сам стек наблюдаемости.
# К Docker API cAdvisor ходит только через socket-proxy (флаг -docker —
# проверено по --help образа v0.52.1; ENV у образа флаги не читает): маунтов
# docker.sock и /var/lib/docker больше нет. Маунты / и /sys — каноничные из
# документации cAdvisor (только чтение): без них не видно cgroup-иерархии, по
# которой находятся контейнеры, — проверено запуском. Без --privileged и
# /dev/kmsg недоступны OOM- и perf-коллекторы, CPU, память, диски и сеть
# остаются. Новые контейнеры cAdvisor находит сам, без подписки на события
# Docker: опрашивает cgroup-иерархию и запрашивает у proxy inspect новых
# (проверено запуском нового контейнера рядом).
resource "docker_container" "cadvisor" {
  count   = var.observability_enabled ? 1 : 0
  name    = "dmc268-cadvisor"
  image   = docker_image.cadvisor[0].image_id
  restart = "unless-stopped"

  memory      = var.observability_memory_mb.cadvisor
  memory_swap = var.observability_memory_mb.cadvisor
  log_driver  = "json-file"
  log_opts    = local.log_opts

  # Без root, без маунта «/» и без capabilities: рекурсивный /rootfs открывал
  # бы unix-сокет Docker (/rootfs/run/docker.sock — полный Docker API, то есть
  # root на хосте) и Env всех контейнеров — socket-proxy это не оправдывает
  # (замечание ревью PR #130, high). Метрики контейнеров — через dockerproxy,
  # хостовые файловые — node-exporter; cgroup v2 в /sys читается nobody.
  user          = "65534"
  read_only     = true
  tmpfs         = { "/tmp" = "rw,noexec,nosuid,size=64m" }
  security_opts = ["no-new-privileges:true"]

  capabilities {
    drop = ["ALL"]
  }

  command = [
    # Имя dockerproxy резолвится в отдельной сети socket-proxy (алиас у
    # docker_container.docker_socket_proxy) — так же контейнер зовут и в
    # docker-compose.yml.
    "--docker=tcp://dockerproxy:2375",
  ]

  volumes {
    host_path      = "/sys"
    container_path = "/sys"
    read_only      = true
  }

  networks_advanced {
    name    = docker_network.observability[0].name
    aliases = ["cadvisor"]
  }

  networks_advanced {
    name = docker_network.observability_docker_proxy[0].name
  }

  depends_on = [docker_container.docker_socket_proxy]
}

# Прокси над Docker API (/var/run/docker.sock) с доступом по белому списку
# эндпоинтов: cAdvisor — единственный клиент, docker.sock контейнерам больше
# не монтируется. Права — минимальный работающий набор (проверено запуском
# cAdvisor через proxy): CONTAINERS=1 — список, inspect и статистика
# контейнеров; INFO=1 — /info и /version, без них cAdvisor падает на проверке
# Docker-фабрики («403 Forbidden» на /v1.45/info) и не отдаёт метрики
# контейнеров вовсе. События Docker (/events) не нужны: новые контейнеры
# находятся опросом cgroup-иерархии. Сеть — отдельная, только proxy и cAdvisor.
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

  env = [
    "CONTAINERS=1",
    "INFO=1",
  ]

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
