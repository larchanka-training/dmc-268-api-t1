# Выходы стека.

# Окружение миграций для шага деплоя (.github/scripts/deploy/migrate.sh):
# строки KEY=VALUE для `docker run --env-file` — то же, что получают api и
# worker, но DATABASE_URL — владельцем базы (local.database_owner_url): схему
# меняют только миграции. Плюс телеметрия (local.telemetry_env: со включённым
# стеком миграции пишут трейс деплоя) и пароли ролей dmc268_monitor и
# dmc268_app — их шаг едет вместе с миграциями (замечание ревью PR #130):
# роли создают миграции 0013 и 0023, а alembic/role_passwords.py сводит пароли с
# секретами на каждом деплое. Один источник пароля с env коллектора
# (observability.tf) и DATABASE_URL api и worker — иначе роль и подключение
# разъедутся. Поэтому адрес базы и пароли не собираются второй раз в bash.
# Чувствительный: в нём пароли; скрипт читает его `tofu output -raw` в файл с
# правами 600 и удаляет файл после запуска.
output "migrate_env" {
  value = join("\n", concat(
    ["DATABASE_URL=${local.database_owner_url}"],
    local.service_env,
    local.telemetry_env,
    [
      "OTEL_RESOURCE_ATTRIBUTES=${local.telemetry_resource},service.instance.id=dmc268-migrations",
      "POSTGRES_MONITOR_PASSWORD=${var.postgres_monitor_password}",
      "POSTGRES_APP_PASSWORD=${var.postgres_app_password}",
    ],
  ))
  sensitive = true
}
