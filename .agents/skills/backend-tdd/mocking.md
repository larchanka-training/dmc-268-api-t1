# Где проходит граница мока

| Зависимость | Что делаем |
|---|---|
| PostgreSQL | настоящая база через `TEST_DATABASE_URL` |
| Свой модуль, функция, адаптер | вызываем как есть |
| Время, идентификаторы, случайность | передаём аргументом |
| Своя очередь за сетью (RabbitMQ) | фейк-адаптер за портом |
| Чужой сервис (Ollama, провайдер VCS) | мок за портом |

Мок своего модуля проверяет, что мы позвали то, что собирались, а не что получилось.

## Аргумент вместо патча

```python
# ХОРОШО
moved = advance(run, ReviewRunStatus.BUILDING_CONTEXT, later)

# ПЛОХО: тест вынужден патчить datetime
def advance(run, status):
    now = datetime.now(UTC)
```

Патч `datetime.now` в тесте — признак того, что правило нарушено в коде.

## Фейк, а не мок с counters

```python
class InMemoryJobQueue:
    def __init__(self) -> None:
        self.enqueued: list[str] = []

    def enqueue(self, run_id: str) -> None:
        self.enqueued.append(run_id)
```

Проверяем итог (`queue.enqueued == [run.id]`), а не число вызовов: счётчики и порядок обращений
описывают реализацию.
