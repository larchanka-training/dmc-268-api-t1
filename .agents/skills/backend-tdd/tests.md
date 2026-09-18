# Хорошие и плохие тесты

Примеры на коде этого репозитория.

## Время аргументом — точное утверждение

```python
def test_advance_stamps_the_supplied_time(run) -> None:
    later = datetime(2026, 9, 9, 12, 5, tzinfo=UTC)
    moved = advance(run, S.BUILDING_CONTEXT, later).unwrap()
    assert moved.last_progress_at == later
```

## Инвариант вместо случая

```python
@pytest.mark.parametrize("terminal", sorted(TERMINAL_STATUSES))
@pytest.mark.parametrize("requested", sorted(ReviewRunStatus))
def test_terminal_states_refuse_everything(terminal: S, requested: S) -> None:
    assert not next_status(terminal, requested).ok
```

Новое состояние в enum попадает под проверку само.

## Побочный канал

```python
# ПЛОХО: знает имя таблицы и колонки
row = uow.session.execute(text("SELECT status FROM review_runs")).one()
assert row.status == "queued"

# ХОРОШО: читает тем же интерфейсом
assert uow.review_runs.get(run.id).status is ReviewRunStatus.QUEUED
```

## Тавтология

```python
# ПЛОХО: пройдёт, даже если deduplicate вернёт вход без изменений
expected = {(f.rule_id, f.anchor) for f in findings}
assert {(f.rule_id, f.anchor) for f in deduplicate(findings)} == expected

# ХОРОШО: ожидаемое задано вручную
kept = deduplicate([finding, replace(finding, id=new_id())])
assert len(kept) == 1
```

## Явный пропуск

```python
pytestmark = [pytest.mark.integration, requires_db]
```

Без `requires_db` тест исчезает из прогона молча.

## Граница

Тест домена не поднимает базу. Тест адаптера проверяет перевод данных, а не бизнес-правило:
перебор вариантов правила в тесте адаптера означает, что правило лежит не в том слое.
