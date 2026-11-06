# Правила ревью: базовая безопасность

Шаблон `.review/rules.md`, не привязанный к стеку: секреты, инъекции, доступ, криптография и
зависимости. Его можно положить в репозиторий целиком или добавить разделы к правилам своего
стека. Абзацы вроде этого сервис модели не передаёт: правилом считается только раздел `##` с
`pattern:` и нумерованными проверками. Как писать свои правила — `docs/RULES_GUIDE.md`.

## Secrets

- pattern: `*`

1. В коде и конфигурации нет ключей, паролей и токенов: они читаются из окружения или хранилища секретов
2. Секрет не попадает в лог, текст ошибки или ответ API
3. В репозиторий не добавляется `.env` с настоящими значениями: только `.env.example` с заглушками

## Injection

- pattern: `*.py`
- pattern: `*.ts`
- pattern: `*.tsx`
- pattern: `*.js`
- pattern: `*.go`
- pattern: `*.java`
- pattern: `*.kt`
- pattern: `*.rb`
- pattern: `*.php`

1. SQL-запрос получает значения параметрами, а не конкатенацией или форматированием строки
2. Команда ОС запускается без оболочки, аргументы передаются списком; ввод пользователя не попадает в shell
3. Путь к файлу из ввода нормализуется и проверяется, что он остаётся внутри разрешённого каталога
4. Недоверенные данные не десериализуются небезопасно: нет `pickle`, `yaml.load` без `SafeLoader`, `eval`
5. Адрес исходящего запроса из ввода пользователя проверяется по списку разрешённых хостов (SSRF)

## Access Control

- pattern: `*.py`
- pattern: `*.ts`
- pattern: `*.js`
- pattern: `*.go`
- pattern: `*.java`
- pattern: `*.kt`
- pattern: `*.rb`
- pattern: `*.php`
- exclude: `*.test.ts`
- exclude: `*_test.go`
- exclude: `tests/`

1. Новый эндпоинт проверяет аутентификацию; открытый без входа эндпоинт помечен явно
2. Доступ к объекту по идентификатору проверяет, что объект принадлежит текущему пользователю или его организации
3. Подписи, токены и хэши паролей сравниваются за постоянное время (`hmac.compare_digest`, `crypto/subtle`)

## Cryptography and Transport

- pattern: `*.py`
- pattern: `*.ts`
- pattern: `*.js`
- pattern: `*.go`
- pattern: `*.java`
- pattern: `*.kt`
- pattern: `*.rb`
- pattern: `*.php`

1. Пароли хэшируются bcrypt, scrypt или Argon2, а не MD5, SHA-1 или SHA-256 без соли
2. Проверка TLS не отключается (`verify=False`, `InsecureSkipVerify`, `rejectUnauthorized: false`)
3. Токены и одноразовые коды генерируются криптостойким генератором (`secrets`, `crypto/rand`, `crypto.getRandomValues`)

## Dependencies and Images

- pattern: `package.json`
- pattern: `pyproject.toml`
- pattern: `requirements*.txt`
- pattern: `go.mod`
- pattern: `Dockerfile`
- pattern: `*.Dockerfile`

1. Новая зависимость добавлена с ограничением версии, а не `*`, `latest` или без версии
2. Базовый образ Docker указан с конкретной версией или digest, а не `latest`
3. Процесс в контейнере запускается не от root (`USER`)
