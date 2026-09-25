#!/usr/bin/env sh
# Чистая функция: (имя пользователя, публичный ключ) -> текст bash-скрипта на stdout.
#
# Ни сети, ни секретов внутри: пароль, если он понадобится sudo, приходит
# готовому скрипту на stdin уже на сервере. Скрипт печатает строки вида
# КЛЮЧ=значение — их разбирает workflow, собирая summary.
set -eu

if [ "$#" -ne 2 ]; then
    echo "использование: $0 <пользователь> <публичный-ключ>" >&2
    exit 2
fi

user_name="$1"
public_key="$2"

# Значения подставляются внутрь одинарных кавычек, поэтому одинарная кавычка в
# них сломала бы скрипт. Ни в имени пользователя, ни в ssh-ключе ей взяться
# неоткуда — но проверить дешевле, чем разбираться потом.
case "$user_name$public_key" in
    *"'"*) echo "одинарная кавычка во входных данных" >&2; exit 2 ;;
esac

cat <<INNER
set -u

USER_NAME='${user_name}'
PUB_KEY='${public_key}'

# --- права -------------------------------------------------------------
# Что доступно, заранее неизвестно, поэтому проверяем по порядку: root,
# беспарольный sudo, sudo с паролем (пароль ждём на stdin), ничего.
if [ "\$(id -u)" -eq 0 ]; then
    PRIVS='root'
    run_sudo() { "\$@"; }
elif sudo -n true 2>/dev/null; then
    PRIVS='sudo без пароля'
    run_sudo() { sudo -n "\$@"; }
elif command -v sudo >/dev/null 2>&1; then
    IFS= read -r SUDO_PW || SUDO_PW=''
    if [ -n "\$SUDO_PW" ] && printf '%s\n' "\$SUDO_PW" | sudo -S -p '' true 2>/dev/null; then
        PRIVS='sudo с паролем'
        run_sudo() { printf '%s\n' "\$SUDO_PW" | sudo -S -p '' "\$@"; }
    else
        PRIVS='нет'
    fi
else
    PRIVS='нет'
fi

echo "PRIVS=\$PRIVS"

if [ "\$PRIVS" = 'нет' ]; then
    # Не ошибка, а результат: дальше нужен человек с root.
    echo 'DOCKER=пропущено, нет прав'
    echo 'GROUP=пропущено, нет прав'
    echo 'KEY=пропущено, нет прав'
    exit 0
fi

# --- Docker ------------------------------------------------------------
if command -v docker >/dev/null 2>&1; then
    echo "DOCKER=уже установлен (\$(docker --version 2>/dev/null || echo 'версия не определена'))"
else
    if run_sudo sh -c 'curl -fsSL https://get.docker.com -o /tmp/get-docker.sh && sh /tmp/get-docker.sh >/dev/null 2>&1 && rm -f /tmp/get-docker.sh'; then
        echo "DOCKER=установлен (\$(docker --version 2>/dev/null || echo 'версия не определена'))"
    else
        echo 'DOCKER=установка не удалась'
    fi
fi

# --- группа docker -----------------------------------------------------
if ! command -v docker >/dev/null 2>&1; then
    echo 'GROUP=пропущено, Docker не установлен'
elif id -nG "\$USER_NAME" 2>/dev/null | tr ' ' '\n' | grep -qx docker; then
    echo 'GROUP=уже в группе'
elif run_sudo usermod -aG docker "\$USER_NAME"; then
    # Членство в группе подхватывается только новой сессией — та, что сейчас
    # открыта, о нём ещё не знает. Проверять надо следующим подключением.
    echo 'GROUP=добавлен, вступит в силу со следующего входа'
else
    echo 'GROUP=добавить не удалось'
fi

# --- публичный ключ ----------------------------------------------------
mkdir -p "\$HOME/.ssh"
chmod 700 "\$HOME/.ssh"
touch "\$HOME/.ssh/authorized_keys"
chmod 600 "\$HOME/.ssh/authorized_keys"

if grep -qxF "\$PUB_KEY" "\$HOME/.ssh/authorized_keys"; then
    echo 'KEY=уже добавлен'
else
    printf '%s\n' "\$PUB_KEY" >> "\$HOME/.ssh/authorized_keys"
    echo 'KEY=добавлен'
fi
INNER
