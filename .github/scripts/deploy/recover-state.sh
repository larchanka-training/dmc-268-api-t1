#!/usr/bin/env bash
# Восстановление состояния OpenTofu после обрыва связи с хранилищем (спека
# backend-delivery, «Развёртывание восстанавливает состояние после обрыва»).
#
# Что оставляет OpenTofu 1.13.2 (s3 + use_lockfile), если хранилище пропало
# посреди apply (проверено на docker:29-dind и RustFS 1.0.3):
# - промежуточная запись состояния падает, и apply останавливается — ресурсы,
#   до которых он не дошёл, не трогаются;
# - несохранённое состояние ложится в `errored.tfstate` рабочего каталога —
#   у `-chdir=infra` это infra/; раннер исчезает вместе с ним;
# - блокировка не снимается: объект `<key>.tflock` остаётся в бакете, а ID
#   в выводе apply нет — он есть только в самом объекте (JSON LockInfo, поле
#   ID; Who — `пользователь@хост` того, кто её взял).
# Следующий деплой без восстановления упирается в блокировку, а после
# force-unlock — в старое состояние: план создаёт заново уже созданные
# контейнеры и падает на конфликте имён.
#
# Поэтому: заново открыть туннель (s3-tunnel.sh), дождаться хранилища,
# записать `errored.tfstate` и снять блокировку — свою: Who совпадает с
# этим раннером. Запись идёт под нашей же блокировкой (`-lock=false`, пока она
# висит): снять её раньше значило бы пустить чужой apply между снятием и
# записью. Блокировка снимается после записи; без `errored.tfstate`
# (состояние записалось, не снялась только блокировка) — только она.
#
# Перед записью — сверка с хранилищем (`tofu state pull`): оборванная запись
# оставляет в нём ту же линию (lineage) и serial меньше нашего — serial растёт
# до отправки и при неудаче не откатывается (internal/states/remote в
# OpenTofu 1.13.2). Другая линия или serial не меньше нашего — запись после
# нашей, не перетираем: `state push` без `-force` отказывает, только если
# линия другая или наш serial меньше, а с тем же перетёр бы. Тот же serial с
# тем же содержимым — хранилище запись приняло, а ответ не дошёл: писать
# нечего.
#
# Чужую блокировку — ручного `tofu` (Who `пользователь@хост`) — скрипт не
# трогает: без `errored.tfstate` это `::notice::` (запуск упал, не дождавшись
# её), с ним — ручная работа. Блокировка другого раннера (Who `runner@…`) —
# мёртвая: деплои идут по одному (concurrency `deploy` в deploy.yml), и раз
# идёт этот, тот уже кончился. Её скрипт снимает, иначе в неё упёрся бы
# каждый следующий деплой. Так же снимается своя, если шаг оборвали
# (INTERRUPTED: отмена или таймаут job'а) — tofu убит и `errored.tfstate` не
# написал. В обоих случаях неизвестно, записалось ли состояние: блокировка
# apply — `::warning::`, что состояние может быть старше стенда; плана —
# `::notice::`, план состояние не пишет.
#
# Несохранённое состояние раннер уносит с собой, поэтому сначала его
# зашифрованная копия кладётся в каталог отчёта (errored.tfstate.enc,
# AES-256 с отдельным случайным ключом STATE_BACKUP_KEY — не паролем
# хранилища: репозиторий публичный, и ключ ничего, кроме копии, не открывает);
# шаг state_artifact в deploy.yml
# загружает её артефактом, если восстановление не вышло. Открытым текстом —
# никогда: в состоянии пароли.
#
# Не вышло — `::error::` с инструкцией (infra/README.md, «Если деплой упал
# посреди apply») и код 1. Итог — в файл state-recovery каталога отчёта для
# summary (scripts/deploy_report.py): строки `ключ=значение`.
#
# Нужны: хост `stand` в ~/.ssh/config (.github/actions/stand-ssh),
# инициализированный infra/ и учётные данные хранилища. Параметры (env):
# REPORT_DIR — каталог отчёта, по умолчанию $RUNNER_TEMP/deploy-report;
# RECOVER_WAIT — сколько секунд ждать, пока хранилище ответит (120);
# INTERRUPTED=true — шаг tofu оборван, а не упал сам;
# STATE_BACKUP_KEY — ключ зашифрованной копии (секрет STATE_BACKUP_KEY_DMC268_T1);
# S3_TUNNEL — скрипт туннеля, по умолчанию .github/scripts/s3-tunnel.sh (тест
# подменяет его: настоящий открывает порт и запускает сторожа).
set -uo pipefail

dir="${REPORT_DIR:-${RUNNER_TEMP:-/tmp}/deploy-report}"
mkdir -p "$dir"
report="$dir/state-recovery"
errored=infra/errored.tfstate
copy="$dir/errored.tfstate.enc"
here="$(cd "$(dirname "$0")" && pwd)"
manual='инструкция — infra/README.md, «Если деплой упал посреди apply»'

: > "$report"
note() { printf '%s=%s\n' "$1" "$2" >> "$report"; }
fail() {
  note result failed
  note reason "$1"
  echo "::error::состояние OpenTofu не восстановлено: $1; $manual"
  exit 1
}

# Копия — до всего остального: шаг могут оборвать посреди ожидания хранилища.
rm -f "$copy"
if [ -e "$errored" ]; then
  if [ -z "${STATE_BACKUP_KEY:-}" ]; then
    echo "::warning::нет ключа STATE_BACKUP_KEY: зашифрованную копию $errored не сохранить"
  elif ! (umask 077 && openssl enc -aes-256-cbc -pbkdf2 -iter 600000 -salt \
      -pass env:STATE_BACKUP_KEY -in "$errored" -out "$copy"); then
    rm -f "$copy"
    echo "::warning::зашифрованная копия $errored не сохранилась"
  fi
fi

# Бакет, ключ и адрес хранилища — из конфигурации бэкенда, которую сохранил
# `tofu init`, а не копией backend.tf. Нет её — init не прошёл, и ни
# состояния, ни блокировки этот запуск не трогал.
backend="$(python3 - <<'PY' 2>/dev/null
import json, urllib.parse
c = json.load(open("infra/.terraform/terraform.tfstate"))["backend"]["config"]
print(c["bucket"], c["key"], c["endpoints"]["s3"], urllib.parse.urlsplit(c["endpoints"]["s3"]).port or "-")
PY
)"
if [ -z "$backend" ]; then
  if [ -e "$errored" ]; then
    fail "есть $errored, но нет конфигурации бэкенда infra/.terraform"
  fi
  note result none
  echo "infra/ не инициализирован: состояние и блокировку этот запуск не трогал"
  exit 0
fi
read -r bucket key endpoint port <<< "$backend"
# Порт — он же локальный конец туннеля; без него туннель не открыть.
[[ "$port" =~ ^[0-9]+$ ]] || fail "в адресе хранилища $endpoint нет порта"

# Туннель сам ждёт ответа хранилища — здесь дольше, чем при обычном открытии:
# хранилище могло зависнуть, а не упасть.
wait_for="${RECOVER_WAIT:-120}"
S3_TUNNEL_WAIT="$wait_for" "${S3_TUNNEL:-$here/../s3-tunnel.sh}" "$port" \
  || fail "туннель не открылся или хранилище не ответило за ${wait_for} с"

# Блокировка: объект есть — его JSON, нет — пусто. Ошибка, кроме «нет
# объекта», — хранилище не отвечает как надо, гадать нельзя.
lock_file="$(mktemp)"
remote_file="$(mktemp)"
trap 'rm -f "$lock_file" "$remote_file"' EXIT
if ! out="$(timeout 60 aws --endpoint-url "$endpoint" s3api get-object \
    --bucket "$bucket" --key "${key}.tflock" "$lock_file" 2>&1 >/dev/null)"; then
  case "$out" in
    *NoSuchKey*|*"Not Found"*|*404*) : > "$lock_file" ;;
    *) fail "блокировку не прочитать: $(printf '%s' "$out" | tr '\n' ' ')" ;;
  esac
fi
read -r lock_id lock_who lock_op lock_created < <(python3 - "$lock_file" <<'PY'
import json, sys
text = open(sys.argv[1]).read().strip()
info = json.loads(text) if text else {}
print(*(info.get(k) or "-" for k in ("ID", "Who", "Operation", "Created")))
PY
) || fail "блокировка — не JSON LockInfo"

me="$(id -un)@$(hostname)"
# Снять блокировку, не зная, записалось ли состояние под ней.
unsure=""
if [ "$lock_id" != "-" ]; then
  note lock "$lock_id"
  if [ "$lock_who" != "$me" ]; then
    note who "$lock_who"
    # Несохранённое состояние под чужой блокировкой не записать — это ручная
    # работа.
    if [ -e "$errored" ]; then
      fail "блокировку $lock_id держит $lock_who, а не этот запуск ($me)"
    fi
    case "$lock_who" in
      # Другой раннер — деплой, который уже кончился (concurrency `deploy`).
      runner@*) unsure=1 ;;
      # Ручной tofu: может работать прямо сейчас, не трогаем. Этот запуск
      # ничего не сломал: он упал, не дождавшись блокировки (-lock-timeout).
      *)
        note result foreign
        echo "::notice::блокировку $lock_id держит $lock_who, а не этот запуск: восстановление её не трогает"
        exit 0
        ;;
    esac
  elif [ ! -e "$errored" ] && [ "${INTERRUPTED:-}" = true ]; then
    unsure=1
  fi
fi

if [ -e "$errored" ]; then
  serial="$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["serial"])' "$errored" 2>/dev/null)" \
    || fail "$errored не читается"
  note serial "$serial"
  # Блокировка остаётся при любом отказе ниже: снять её без записи — пустить
  # следующий apply на старом состоянии.
  timeout 60 tofu -chdir=infra state pull > "$remote_file" \
    || fail "состояние в хранилище не прочитать (tofu state pull)"
  verdict="$(python3 - "$errored" "$remote_file" <<'PY'
import json, sys
ours = json.load(open(sys.argv[1]))
text = open(sys.argv[2]).read().strip()
theirs = json.loads(text) if text else None
if theirs is None or theirs["serial"] < ours["serial"] and theirs["lineage"] == ours["lineage"]:
    print("push")
elif theirs["lineage"] != ours["lineage"]:
    print(f"в хранилище состояние другой линии ({theirs['lineage']})")
elif theirs["serial"] == ours["serial"] and all(theirs.get(k) == ours.get(k) for k in ("resources", "outputs")):
    print("written")
else:
    print(f"в хранилище состояние serial {theirs['serial']}, записанное после нашего")
PY
)" || fail "состояние в хранилище — не JSON состояния OpenTofu"
  case "$verdict" in
    push)
      # Своя блокировка висит — пишем под ней; нет её — берём, как обычно.
      if [ "$lock_id" != "-" ]; then lock_flag=-lock=false; else lock_flag=-lock-timeout=2m; fi
      timeout 120 tofu -chdir=infra state push -no-color "$lock_flag" errored.tfstate \
        || fail "tofu state push не прошёл (причина в выводе выше)"
      echo "состояние serial $serial записано в хранилище"
      ;;
    written) echo "состояние serial $serial уже в хранилище: запись дошла, не дошёл ответ" ;;
    *) fail "$verdict — не перетираем его несохранённым serial $serial" ;;
  esac
  # В файле состояния пароли открытым текстом: записано — убрать, и копию тоже.
  rm -f "$errored" "$copy"
fi

if [ "$lock_id" != "-" ]; then
  if ! timeout 60 tofu -chdir=infra force-unlock -no-color -force "$lock_id"; then
    fail "блокировка $lock_id не снялась"
  fi
fi

if [ -n "$unsure" ]; then
  note operation "$lock_op"
  note created "$lock_created"
  note result released
  what="блокировка $lock_id ($lock_who, $lock_op, $lock_created) оборвавшегося запуска снята"
  if [ "$lock_op" = OperationTypePlan ]; then
    echo "::notice::$what: план состояние не пишет, оно не пострадало"
  else
    echo "::warning::$what, а записалось ли его состояние, неизвестно: если план перезапуска создаёт контейнеры, которые на сервере уже есть, состояние старше стенда — $manual, шаг 4"
  fi
elif [ -n "${serial:-}" ]; then note result restored
elif [ "$lock_id" != "-" ]; then note result unlocked
else note result none
fi
exit 0
