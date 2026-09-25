terraform {
  # Пол, а не пин: CI ставит OpenTofu 1.12.6, его и проверяем. Занижать
  # смысла нет — состояние, записанное 1.12, старая версия уже не прочитает,
  # так что пол ниже проверяемого обещал бы работоспособность, которую никто
  # не подтверждал.
  required_version = ">= 1.12.0"

  required_providers {
    docker = {
      source  = "kreuzwerker/docker"
      version = "~> 3.0"
    }
  }
}

provider "docker" {
  host = var.docker_host

  # Образ API лежит в приватном пакете репозитория. Учётные данные живут один
  # прогон (GITHUB_TOKEN), поэтому на сервере не нужно ни `docker login`, ни
  # долгоживущий токен, о ротации которого потом некому помнить.
  registry_auth {
    address  = "ghcr.io"
    username = var.registry_username
    password = var.registry_password
  }
}
