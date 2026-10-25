terraform {
  # Пол, а не пин: CI ставит OpenTofu 1.13.2, его и проверяем. Занижать
  # смысла нет — пол ниже проверяемого обещал бы работоспособность, которую
  # никто не подтверждал.
  required_version = ">= 1.13.0"

  required_providers {
    docker = {
      # Точная версия закреплена в .terraform.lock.hcl; команда его пересборки
      # описана в README.md.
      source  = "kreuzwerker/docker"
      version = "~> 4.6"
    }
  }
}

# Учётных данных реестра у провайдера нет: образ API из приватного пакета
# скачивает migrate.sh до apply (вход в ghcr — на раннере, на сервере токен не
# остаётся), а образы PostgreSQL и RabbitMQ публичные. Сам провайдер тянет
# образ контейнера, только если того нет на сервере (README.md, «Деплой»).
provider "docker" {
  host = var.docker_host
}
