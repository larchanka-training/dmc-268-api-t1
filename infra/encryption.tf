# Шифрование состояния и плана (#147): в состоянии пароли базы и брокера, ключ
# GitHub App, секрет вебхука и ключ шифрования токенов — доступ к бакету давал бы
# все секреты стенда. Фраза — секрет STATE_PASSPHRASE_DMC268_T1 в environment
# `stand`, её копия хранится вне GitHub: без фразы состояние не восстановить
# (infra/README.md, «Шифрование состояния»).
#
# Шаг 1: fallback на открытое состояние — первый apply читает старый открытый
# объект и пишет его уже зашифрованным (plan не переписывает). Шаг 2 — убрать
# fallback и включить `enforced = true`.
#
# Фраза нужна уже `tofu init` с бэкендом; `init -backend=false` и `validate`
# идут без неё (CI). Переменная — здесь, а не в variables.tf: файл
# самодостаточен, и восстановление состояния (recover-state.sh) расшифровывает
# errored.tfstate копией одного этого файла.
#
# Имена key_provider и method не менять: они записаны в метаданные
# зашифрованного объекта. Смена фразы — новой парой с прежней в fallback.

variable "state_passphrase" {
  description = "Фраза шифрования состояния и плана OpenTofu (секрет STATE_PASSPHRASE_DMC268_T1)."
  type        = string
  sensitive   = true

  validation {
    condition     = length(var.state_passphrase) >= 16
    error_message = "state_passphrase - не короче 16 символов (требование pbkdf2; openssl rand -base64 48); секрет STATE_PASSPHRASE_DMC268_T1 не задан или короткий."
  }
}

terraform {
  encryption {
    key_provider "pbkdf2" "main" {
      passphrase = var.state_passphrase
    }

    method "aes_gcm" "main" {
      keys = key_provider.pbkdf2.main
    }

    method "unencrypted" "legacy" {}

    state {
      method = method.aes_gcm.main
      fallback {
        method = method.unencrypted.legacy
      }
    }

    # План тоже несёт значения переменных и атрибутов, пароли в том числе.
    # `tofu show -json` с фразой его читает (plan.sh, infra.sh).
    plan {
      method = method.aes_gcm.main
      fallback {
        method = method.unencrypted.legacy
      }
    }
  }
}
