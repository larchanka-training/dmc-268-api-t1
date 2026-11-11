#!/usr/bin/env bash
# Работают ли воркеры после выката (спека backend-delivery, «Развёртывание
# проверяет, что воркер работает»): каждый экземпляр, сколько их задаёт
# worker_replicas (#99).
#
# HTTP у воркера нет, поэтому живость — это состояние контейнера. «Запущен»
# мало: при падении Docker перезапускает его, и в момент проверки он снова
# запущен. Живыми считаем экземпляры, которые 30 секунд подряд запущены и ни
# разу не перезапустились; опрос каждые 5 секунд видит и быстрый рестарт — по
# выросшему счётчику. Окно длиннее жизни процесса ловит любую петлю падений с
# периодом до 30 секунд: после падения процесса, прожившего больше 10 секунд,
# Docker сбрасывает задержку и поднимает его сразу, и короткое окно такую
# петлю пропускало. Рестарты в начале, пока брокер не готов (depends_on его не
# ждёт), окно не засчитывает и начинает заново; на всё — до 90 секунд.
#
# Экземпляры ищутся по имени на сервере: dmc268-worker и dmc268-worker-<N>
# (local.worker_names в infra/app.tf). Снимок опроса — все экземпляры разом, по
# строке «имя запущен рестарты»; окно идёт, пока снимок не меняется, все
# запущены и на сервере ровно ожидаемые имена (dmc268-worker … dmc268-worker-N),
# а не просто столько же: оставшийся от ручного запуска dmc268-worker-3 не
# заменит недостающий dmc268-worker-2. Рестарт любого, пропавший или лишний
# экземпляр начинает окно заново.
#
# Не устоялись — печатает хвост лога каждого экземпляра и падает.
#
# `-e` нет намеренно: пустой ответ (контейнеров нет, ssh упал) — это «ещё не
# устоялся», очередной опрос, а не конец проверки.
#
# Нужен хост `stand` в ~/.ssh/config (.github/actions/stand-ssh). Параметры
# (env): TF_VAR_worker_replicas — сколько экземпляров ждать; не задан — 1, как
# дефолт переменной в infra/variables.tf.
set -uo pipefail

expected="${TF_VAR_worker_replicas:-1}"
pattern='^dmc268-worker(-[0-9]+)?$'

# Ожидаемые имена по правилу local.worker_names, отсортированные как снимок.
expected_names="$(
  echo dmc268-worker
  i=2
  while [ "$i" -le "$expected" ]; do echo "dmc268-worker-$i"; i=$((i + 1)); done
)"
expected_names="$(printf '%s\n' "$expected_names" | sort)"

# Имена экземпляров на сервере, по одному в строке. Фильтр docker — по
# подстроке, точное имя сверяет grep.
names() {
  ssh stand "docker ps -a --filter name=dmc268-worker --format '{{.Names}}'" 2>/dev/null \
    | grep -E "$pattern" | sort
}

# Снимок: «имя запущен рестарты» по экземпляру, отсортированный по имени.
state() {
  local found
  found="$(names | tr '\n' ' ')"
  [ -n "$found" ] || return 0
  # shellcheck disable=SC2029 # имена — с сервера, раскрываются здесь намеренно
  ssh stand "docker inspect -f '{{.Name}} {{.State.Running}} {{.RestartCount}}' $found 2>/dev/null" \
    | sed 's#^/##' | grep -E '^dmc268-worker(-[0-9]+)? (true|false) [0-9]+$' | sort
}

# Снимок годится в окно: на сервере ровно ожидаемые экземпляры, и все запущены.
healthy() {
  [ -n "$1" ] \
    && [ "$(printf '%s\n' "$1" | awk '{print $1}')" = "$expected_names" ] \
    && ! printf '%s\n' "$1" | grep -q ' false '
}

hold=30
started="$(date +%s)"
last=""
stable_since="$started"
while [ $(( $(date +%s) - started )) -lt 90 ]; do
  now="$(date +%s)"
  current="$(state)"
  if healthy "$current" && [ "$current" = "$last" ]; then
    if [ $(( now - stable_since )) -ge "$hold" ]; then
      echo "воркеры работают ${hold} с без рестартов (${expected} шт.), рестартов всего:"
      printf '%s\n' "$current" | awk '{print "  " $1 ": " $3}'
      exit 0
    fi
  else
    [ -n "$last" ] && echo "воркеры ещё не устоялись: было '$(echo "$last" | tr '\n' ';')', стало '$(echo "$current" | tr '\n' ';')'"
    last="$current"
    stable_since="$now"
  fi
  sleep 5
done

found="$(names)"
missing="$(printf '%s\n' "$expected_names" | grep -vxF -f <(printf '%s\n' "$found" | grep .) | tr '\n' ' ')"
extra="$(printf '%s\n' "$found" | grep . | grep -vxF -f <(printf '%s\n' "$expected_names") | tr '\n' ' ')"
echo "::error::воркеры не проработали ${hold} с без рестартов за полторы минуты: ждали ${expected} шт., на сервере — $(printf '%s' "$found" | grep -c .) ($(echo "$found" | tr '\n' ' '))${missing:+; нет: ${missing% }}${extra:+; лишние: ${extra% }}"
for name in $found; do
  echo "--- ${name}: последние строки лога"
  # shellcheck disable=SC2029 # имя — с сервера, раскрывается здесь намеренно
  ssh stand "docker logs --tail 50 ${name} 2>&1" || true
done
exit 1
