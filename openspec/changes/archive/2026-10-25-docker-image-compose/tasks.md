## 1. Образ

- [x] 1.1 Dockerfile: multi-stage, `UV_COMPILE_BYTECODE=1`, рантайм без uv, байткод `app` и `alembic`, без pip, не root
- [x] 1.2 `.dockerignore`: `**/__pycache__`, `**/*.pyc`, каталоги вне `COPY`

## 2. Compose и окружение

- [x] 2.1 Порты на `127.0.0.1`, сервис `migrate`, `depends_on: service_completed_successfully` у API и воркера, Redis в профиле
- [x] 2.2 `.env.example` сверен с `Settings`
- [x] 2.3 README: запуск

## 3. CI

- [x] 3.1 Job `image`: hadolint и trivy (HIGH, CRITICAL, `ignore-unfixed`), actions прибиты по SHA
