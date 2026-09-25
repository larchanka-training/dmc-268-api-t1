#!/usr/bin/env sh
# Чистая функция: (имя контейнера, имя тома, порт, образ) -> текст скрипта.
#
# Учётных данных внутри нет: отрендеренный скрипт ждёт их на stdin уже на
# сервере и кладёт во временный env-file. В аргументы `docker run` они не
# попадают — иначе были бы видны в `ps` любому процессу на сервере.
#
# Скрипт печатает строки вида КЛЮЧ=значение, их разбирает workflow.
set -eu

if [ "$#" -ne 4 ]; then
    echo "использование: $0 <контейнер> <том> <порт> <образ>" >&2
    exit 2
fi

container="$1"
volume="$2"
port="$3"
image="$4"

case "$container$volume$port$image" in
    *"'"*) echo "одинарная кавычка во входных данных" >&2; exit 2 ;;
esac

cat <<INNER
set -u
umask 077

NAME='${container}'
VOLUME='${volume}'
PORT='${port}'
IMAGE='${image}'

ENV_FILE="\$(mktemp)"
trap 'rm -f "\$ENV_FILE"' EXIT
cat > "\$ENV_FILE"

# Три состояния вместо двух: контейнера нет, есть но остановлен, работает.
# Остановленный надо поднять, а не пересоздавать — в томе лежит состояние
# Terraform, и терять его из-за перезагрузки сервера было бы обидно.
STATE="\$(docker inspect -f '{{.State.Running}}' "\$NAME" 2>/dev/null || echo missing)"

case "\$STATE" in
    true)
        echo 'MINIO=уже запущен'
        ;;
    false)
        if docker start "\$NAME" >/dev/null 2>&1; then
            echo 'MINIO=был остановлен, запущен'
        else
            echo 'MINIO=запустить не удалось'
        fi
        ;;
    *)
        # Порт публикуется только на петлю: снаружи к хранилищу дороги нет,
        # CI приходит через SSH-туннель.
        if docker run -d \
            --name "\$NAME" \
            --restart unless-stopped \
            -p 127.0.0.1:"\$PORT":9000 \
            -v "\$VOLUME":/data \
            --env-file "\$ENV_FILE" \
            "\$IMAGE" server /data >/dev/null 2>&1; then
            echo 'MINIO=создан'
        else
            echo 'MINIO=создать не удалось'
        fi
        ;;
esac

# Образ пока не закреплён по digest. Печатаем его, чтобы закрепить следующей
# задачей, а не гадать, что там приехало.
echo "IMAGE_DIGEST=\$(docker inspect -f '{{index .RepoDigests 0}}' "\$IMAGE" 2>/dev/null || echo 'не определён')"
echo "LISTENS=\$(docker port "\$NAME" 9000 2>/dev/null || echo 'порт не опубликован')"
INNER
