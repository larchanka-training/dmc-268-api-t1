# Шифрование состояния и плана (#147): в состоянии пароли базы и брокера, ключ
# GitHub App, секрет вебхука и ключ шифрования токенов — доступ к бакету давал бы
# все секреты стенда. Фраза — секрет STATE_PASSPHRASE_DMC268_T1 в environment
# `stand`, её копия хранится вне GitHub: без фразы состояние не восстановить
# (infra/README.md, «Шифрование состояния»).
#
# У `state` fallback на открытое состояние — первый apply читает старый
# открытый объект и пишет его уже зашифрованным (plan не переписывает). У `plan`
# fallback нет: план создаётся и применяется в одном job'е, старых открытых
# планов не бывает. Шаг 2 (убрать fallback, `enforced = true`, удалить старые
# открытые версии в бакете) отменён в #147: хранилище слушает только петлю
# сервера. Вернуть его до выноса хранилища или его копии за пределы сервера
# (infra/README.md, «Шифрование состояния»). Откат — не revert, а `state` наоборот (infra/README.md,
# «Шифрование состояния», «Откат»): иначе зашифрованное состояние не прочитать.
#
# Фраза нужна уже `tofu init` с бэкендом; `init -backend=false` и `validate`
# идут без неё (CI). Переменная — здесь, а не в variables.tf: файл
# самодостаточен, и восстановление состояния (recover-state.sh) расшифровывает
# errored.tfstate копией одного этого файла.
#
# Имена key_provider и method не менять: они записаны в метаданные
# зашифрованного объекта. Смена фразы — новой парой с прежней в fallback
# (`unencrypted` — во вложенном fallback: infra/README.md, «Ротация фразы»).

variable "state_passphrase" {
  description = "Фраза шифрования состояния и плана OpenTofu (секрет STATE_PASSPHRASE_DMC268_T1)."
  type        = string
  sensitive   = true

  validation {
    condition     = length(var.state_passphrase) >= 16
    error_message = "state_passphrase - не короче 16 символов (требование pbkdf2; openssl rand -base64 48); секрет STATE_PASSPHRASE_DMC268_T1 не задан или короткий."
  }

  # Плейсхолдер из terraform.tfvars.example длиннее 16 символов, а tfvars
  # перекрывает TF_VAR_state_passphrase: без этой проверки состояние молча
  # шифровалось бы плейсхолдером.
  validation {
    condition     = !startswith(var.state_passphrase, "CHANGE_ME")
    error_message = "state_passphrase - плейсхолдер из terraform.tfvars.example; фраза задаётся через TF_VAR_state_passphrase, строку в terraform.tfvars удалите."
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
    }
  }
}
