## 1. Домен: чанки и отбор

- [x] 1.1 Чистая функция `build_chunks(windows, limits)` в `app/domain/profile.py` — окно окружения в черновик чанка (путь, границы, коммит, sha256-дайджест; сверхлимитные окна пропускает); проверка: unit-тест без базы и сети в `tests/domain/test_profile.py`
- [x] 1.2 Чистая функция `pick_similar(scored, limits)` — top-k с бюджетом байтов, дедуп по дайджесту, отсечение дайджестов текущих окон; проверка: unit-тест на лимиты и приоритет более близких
- [x] 1.3 Чистая функция `redact(text)` — минимальный набор паттернов секретов (AWS/GitHub-токены, private key, высокоэнтропийные строки); проверка: unit-тест — известные секреты заменяются, обычный код не трогается

## 2. Схема и миграция

- [x] 2.1 Зависимости `pgvector` и `ollama` через `uv add`; проверка: `uv sync --all-extras` и `uv run pytest` проходят
- [x] 2.2 Модель `RepoCodeChunk` и маппер (`app/infrastructure/db/models.py`, `mappers.py`): UUIDv7 PK, FK с `ON DELETE RESTRICT` на `repositories` и `review_runs`, уникальность `(repository_id, content_digest)`, столбец `vector(N)`; проверка: тест скомпилированного DDL
- [x] 2.3 Миграция Alembic (одна ревизия): `CREATE EXTENSION IF NOT EXISTS vector`, таблица `repo_code_chunks` с ограничениями; проверка: интеграционный тест `upgrade head → downgrade → upgrade` на одноразовой базе, `uv run alembic heads` показывает один head

## 3. Порты и адаптеры

- [x] 3.1 Порт `EmbeddingGateway` (Protocol, `app/application/ports/`) с батчевым `embed(texts)`; проверка: тест контейнера — у порта есть адаптер и вызывающий код
- [x] 3.2 Адаптер `OllamaEmbeddingGateway` (`app/infrastructure/`) — батч-вызов Ollama, сверка размерности ответа с `EMBEDDING_DIMENSION`; проверка: интеграционный тест с живым Ollama (маркер `integration`)
- [x] 3.3 Порт `CodeProfileRepo` и адаптер `SqlAlchemyCodeProfileRepo` — `add_many` c `ON CONFLICT DO NOTHING`, `search` по косинусному расстоянию с фильтрами репозитория и модели и исключением дайджестов, редакция секретов `redact` в шве адаптера; проверка: интеграционные тесты на одноразовой базе (идемпотентность вставки, границы поиска)

## 4. Use case'ы и конфигурация

- [x] 4.1 Use case `RetrieveSimilarCode` (`app/application/`) — батч-вложение текущих окон, поиск, отбор `pick_similar`, уровень `similar`; любой сбой — пустой уровень без влияния на прогон; проверка: unit-тест на фейках портов, включая недоступный эмбеддер
- [x] 4.2 Use case `IngestProfile` (`app/application/`) — `build_chunks` → `redact` → вложение → `add_many`; сбой записи не меняет исход прогона; проверка: unit-тест на фейках портов
- [x] 4.3 Настройки `EMBEDDING_MODEL`, `EMBEDDING_DIMENSION`, `OLLAMA_BASE_URL` и лимиты профиля в `app/config.py`, привязка портов в `app/infrastructure/container.py`; проверка: тест — отсутствующая настройка останавливает старт с именем параметра

## 5. Документация и инфраструктура

- [x] 5.1 SYSTEM_DESIGN.md: уточнить §2.1 «контекст без клона» (профиль из уже увиденных фрагментов в pgvector; отдельной векторной БД по-прежнему нет), добавить уровень `similar` в §5 и `EmbeddingGateway` в диаграмму §1/§2; проверка: разделы не противоречат BACKEND_ARCHITECTURE.md
- [x] 5.2 BACKEND_ARCHITECTURE.md: порты `EmbeddingGateway` и `CodeProfileRepo` в таблицу реализованных, шов вызова use case'ов из пайплайна ревью; erd.md: таблица `repo_code_chunks` со связями и ограничениями; проверка: каждый порт из кода присутствует в документе
- [x] 5.3 Локальный стек: образ PostgreSQL содержит pgvector (правка `infra/` при необходимости); проверка: `alembic upgrade head` проходит на локальном стеке

## 6. Интеграционные проверки

- [x] 6.1 Полный прогон проверок: `uv run ruff check .`, `uv run lint-imports`, `uv run pytest` — зелёные; при заданном `TEST_DATABASE_URL` пропущенных integration-тестов нет
