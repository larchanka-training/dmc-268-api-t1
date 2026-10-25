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
# записать `errored.tfstate` и снять блокировку — только свою: Who совпадает с
# этим раннером. Запись идёт под нашей же блокировкой (`-lock=false`, пока она
# висит): снять её раньше значило бы пустить чужой apply между снятием и
# записью. Чужую блокировку скрипт не трогает: без `errored.tfstate` это
# `::notice::` (запуск упал, не дождавшись её), с ним — ручная работа.
# `state push` без `-force` сам отказывает, если в хранилище
# состояние другой линии (lineage) или с serial не меньше нашего, — так более
# новое состояние не перетирается. Блокировка снимается после записи; без
# `errored.tfstate` (состояние записалось, не снялась только блокировка) —
# только она.
#
# Не вышло — `::error::` с инструкцией (infra/README.md, «Если деплой упал
# посреди apply») и код 1. Итог — в файл state-recovery каталога отчёта для
# summary (scripts/deploy_report.py): строки `ключ=значение`.
#
# Нужны: хост `stand` в ~/.ssh/config (.github/actions/stand-ssh),
# инициализированный infra/ и учётные данные хранилища. Параметры (env):
# REPORT_DIR — каталог отчёта, по умолчанию $RUNNER_TEMP/deploy-report;
# RECOVER_WAIT — сколько секунд ждать, пока хранилище ответит (120);
# S3_TUNNEL — скрипт туннеля, по умолчанию .github/scripts/s3-tunnel.sh (тест
# подменяет его: настоящий открывает порт и запускает сторожа).
set -uo pipefail

dir="${REPORT_DIR:-${RUNNER_TEMP:-/tmp}/deploy-report}"
mkdir -p "$dir"
report="$dir/state-recovery"
errored=infra/errored.tfstate
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

# Бакет, ключ и адрес хранилища — из конфигурации бэкенда, которую сохранил
# `tofu init`, а не копией backend.tf. Нет её — init не прошёл, и ни
# состояния, ни блокировки этот запуск не трогал.
backend="$(python3 - <<'PY' 2>/dev/null
import json, urllib.parse
c = json.load(open("infra/.terraform/terraform.tfstate"))["backend"]["config"]
print(c["bucket"], c["key"], c["endpoints"]["s3"], urllib.parse.urlsplit(c["endpoints"]["s3"]).port)
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

"${S3_TUNNEL:-$here/../s3-tunnel.sh}" "$port" || fail "туннель к хранилищу не открылся"

wait_for="${RECOVER_WAIT:-120}"
started="$(date +%s)"
until curl -s -o /dev/null --max-time 10 "$endpoint/"; do
  if [ $(( $(date +%s) - started )) -ge "$wait_for" ]; then
    fail "хранилище не ответило за ${wait_for} с"
  fi
  sleep 5
done

# Блокировка: объект есть — его JSON, нет — пусто. Ошибка, кроме «нет
# объекта», — хранилище не отвечает как надо, гадать нельзя.
lock_file="$(mktemp)"
trap 'rm -f "$lock_file"' EXIT
if ! out="$(timeout 60 aws --endpoint-url "$endpoint" s3api get-object \
    --bucket "$bucket" --key "${key}.tflock" "$lock_file" 2>&1 >/dev/null)"; then
  case "$out" in
    *NoSuchKey*|*"Not Found"*|*404*) : > "$lock_file" ;;
    *) fail "блокировку не прочитать: $(printf '%s' "$out" | tr '\n' ' ')" ;;
  esac
fi
read -r lock_id lock_who < <(python3 - "$lock_file" <<'PY'
import json, sys
text = open(sys.argv[1]).read().strip()
info = json.loads(text) if text else {}
print(info.get("ID", "-"), info.get("Who", "-"))
PY
) || fail "блокировка — не JSON LockInfo"

me="$(id -un)@$(hostname)"
if [ "$lock_id" != "-" ]; then
  note lock "$lock_id"
  if [ "$lock_who" != "$me" ]; then
    # Чужую блокировку не трогаем. Несохранённое состояние под ней не
    # записать — это ручная работа; без него этот запуск ничего не сломал:
    # он упал, не дождавшись чужой блокировки (-lock-timeout).
    note who "$lock_who"
    if [ -e "$errored" ]; then
      fail "блокировку $lock_id держит $lock_who, а не этот запуск ($me)"
    fi
    note result foreign
    echo "::notice::блокировку $lock_id держит $lock_who, а не этот запуск: восстановление её не трогает"
    exit 0
  fi
fi

if [ -e "$errored" ]; then
  serial="$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["serial"])' "$errored" 2>/dev/null)" \
    || fail "$errored не читается"
  note serial "$serial"
  # Своя блокировка висит — пишем под ней; нет её — берём, как обычно.
  if [ "$lock_id" != "-" ]; then lock_flag=-lock=false; else lock_flag=-lock-timeout=2m; fi
  if ! timeout 120 tofu -chdir=infra state push -no-color "$lock_flag" errored.tfstate; then
    # Блокировка остаётся: снять её без записи — пустить следующий apply на
    # старом состоянии.
    fail "tofu state push не прошёл (причина в выводе выше: без -force он отказывает, если в хранилище состояние другой линии или serial не меньше $serial)"
  fi
  # В файле состояния пароли открытым текстом: записано — убрать.
  rm -f "$errored"
  echo "состояние serial $serial записано в хранилище"
fi

if [ "$lock_id" != "-" ]; then
  if ! timeout 60 tofu -chdir=infra force-unlock -no-color -force "$lock_id"; then
    fail "блокировка $lock_id не снялась"
  fi
fi

if [ -n "${serial:-}" ]; then note result restored
elif [ "$lock_id" != "-" ]; then note result unlocked
else note result none
fi
exit 0
