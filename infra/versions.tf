terraform {
  # Пол, а не пин: CI ставит OpenTofu 1.10.5, его и проверяем. Прежние
  # ">= 1.5.0" занижали требование — блок `endpoints` в бэкенде s3 появился
  # в 1.6, и на 1.5 это вылезало бы ошибкой схемы, а не внятным сообщением
  # про версию.
  required_version = ">= 1.10.0"

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
