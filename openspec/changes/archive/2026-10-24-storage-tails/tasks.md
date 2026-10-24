## 1. Тесты (красные)

- [x] 1.1 Второй комментарий с тем же `provider_comment_id` отклоняется (`tests/db/test_constraints.py`)
- [x] 1.2 Мутация `body` исходной сущности после `add` не меняет сохранённое (`tests/db/test_adapters.py`)
- [x] 1.3 Прогон, закоммиченный между `COUNT` и страницей, не попадает ни в `total`, ни на страницу; уровень изоляции не утекает в следующую транзакцию (`tests/db/test_run_list_adapter.py`)

## 2. Реализация

- [x] 2.1 Миграция 0011 и `UniqueConstraint` в модели `PublishedCommentRow`
- [x] 2.2 `copy.deepcopy(payload.body)` в `SqlAlchemyContextPayloadRepo.add`
- [x] 2.3 REPEATABLE READ в `SqlAlchemyReviewRunRepo.list_page`
- [x] 2.4 `docs/erd.md`
