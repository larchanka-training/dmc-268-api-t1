# Выходы стека.

# Окружение миграций для шага деплоя (.github/scripts/deploy/migrate.sh):
# строки KEY=VALUE для `docker run --env-file` — ровно local.app_env, то же,
# что получают api и worker, поэтому адрес базы и пароли не собираются второй
# раз в bash. Чувствительный: в нём пароли базы и брокера; скрипт читает его
# `tofu output -raw` в файл с правами 600 и удаляет файл после запуска.
output "migrate_env" {
  value     = join("\n", local.app_env)
  sensitive = true
}
