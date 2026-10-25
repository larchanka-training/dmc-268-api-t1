# Выходы стека.

# Окружение миграций для шага деплоя (.github/scripts/deploy/migrate.sh):
# строки KEY=VALUE для `docker run --env-file` — то же, что получают api и
# worker (local.app_env), плюс телеметрия (local.telemetry_env: со включённым
# стеком миграции пишут трейс деплоя) и пароль роли мониторинга — шаг роли
# едет вместе с миграциями (замечание ревью PR #130), роль dmc268_monitor
# создаёт миграция 0013, коллектор подключается под ней. Один источник пароля
# с env коллектора (observability.tf) — иначе роль и подключение разъедутся.
# Поэтому адрес базы и пароли не собираются второй раз в bash. Чувствительный:
# в нём пароли базы, брокера и роли мониторинга; скрипт читает его
# `tofu output -raw` в файл с правами 600 и удаляет файл после запуска.
output "migrate_env" {
  value = join("\n", concat(
    local.app_env,
    local.telemetry_env,
    [
      "OTEL_RESOURCE_ATTRIBUTES=${local.telemetry_resource},service.instance.id=dmc268-migrations",
      "POSTGRES_MONITOR_PASSWORD=${var.postgres_monitor_password}",
    ],
  ))
  sensitive = true
}
