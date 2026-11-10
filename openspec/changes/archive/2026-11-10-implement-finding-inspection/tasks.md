## 1. Домен

- [x] 1.1 `Finding.verdict: Verdict | None` в `app/domain/entities.py`; тест: замечание без вердикта по умолчанию.
- [x] 1.2 Чистая функция `file_context_of(payloads, file_path)` в `app/domain/file_context.py`: первый пакет с телом и файлом, иначе `None`; тесты без базы - есть, тело стёрто, файла нет, несколько частей.

## 2. Запись вердикта

- [x] 2.1 `review_pipeline`: оставленное замечание записывается с вердиктом из `verification.applied` (`replace(finding, verdict=...)`); тест конвейера - вердикт `keep` и `drop` у серьёзной находки; без вердиктов при `unavailable`.

## 3. База

- [x] 3.1 Модель `FindingRow`: `verifier_verdict` (enum `verifier_verdict`), `verifier_reason`, CHECK «оба или ни одного»; маппер и репозиторий читают и пишут.
- [x] 3.2 Миграция Alembic: enum, два `NULL`-столбца, CHECK; откат удаляет их и тип; тест миграции (откат и накат без остатков типа).
- [x] 3.3 Интеграционный тест адаптера: замечание с вердиктом читается тем же, CHECK отклоняет половину пары.

## 4. API

- [x] 4.1 `get_finding_inspection` отвечает `200`: итог верификации прогона, вердикт замечания, контекст из `uow.context_payloads`; убрать `ERRORS_501_NOT_IMPLEMENTED` и описание заглушки.
- [x] 4.2 Тесты `tests/test_reviews_api.py`: все сценарии спеки - с вердиктом, `drop`, без верификатора, тело стёрто, секрет-метка, чужая находка, невидимый прогон, неверный id.
- [x] 4.3 `tests/test_problem_details.py`: код `NOT_IMPLEMENTED` больше ничем не достижим - в ожидаемое множество кодов добавлен исключением.
- [x] 4.4 Экспорт контракта: `uv run python scripts/export_openapi.py`.

## 5. Документы и архив

- [x] 5.1 `docs/erd.md`: столбцы вердикта у `findings`, номер миграции.
- [x] 5.2 Проверки: `ruff`, `lint-imports`, `mypy`, `pytest` (с базой), `openspec validate --all --strict`; `openspec archive` change после закрытия задач.
