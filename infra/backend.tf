# Состояние лежит в S3-совместимом хранилище на нашем же сервере, доступном только через
# SSH-туннель: endpoint смотрит на localhost, потому что туннель поднимает
# тот, кто запускает tofu. Контейнер хранилища не входит в этот стек и не
# удаляется `tofu destroy` — он не может, иначе стёр бы состояние, которым
# управляет сам себя. Подробности в README.md.
#
# Проверки региона, учётных данных и метаданных отключены: они про AWS, а у
# нас S3-совместимое хранилище без региона и без сервиса метаданных.
terraform {
  backend "s3" {
    bucket = "dmc268-tfstate"
    key    = "api/terraform.tfstate"
    region = "us-east-1"

    endpoints = {
      s3 = "http://127.0.0.1:9000"
    }

    use_path_style              = true
    skip_credentials_validation = true
    skip_metadata_api_check     = true
    skip_region_validation      = true
    skip_requesting_account_id  = true
    skip_s3_checksum            = true
  }
}
