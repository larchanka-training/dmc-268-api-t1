"""Срезы метрик из глобального провайдера в память: приращения между сборами.

Провайдер метрик глобальный на сессию (tests/conftest.py) и приборы
кумулятивны, поэтому тесты бизнес-сигналов сверяют приращение ряда между
двумя сборами, а не абсолют.
"""

from typing import Any

from opentelemetry.sdk.metrics.export import (
    HistogramDataPoint,
    InMemoryMetricReader,
    NumberDataPoint,
)

# Срез приборов: {(имя, метки): (значение|наблюдения, сумма|None)}. У счётчика
# сумма — None; у гистограммы значение — число наблюдений, сумма — их итог.
type Snapshot = dict[tuple[str, tuple[tuple[str, Any], ...]], tuple[float, float | None]]


def metric_snapshot(reader: InMemoryMetricReader) -> Snapshot:
    """Срезать значения всех приборов провайдера на текущий момент."""
    snapshot: Snapshot = {}
    data = reader.get_metrics_data()
    if data is None:
        return snapshot
    for resource_metrics in data.resource_metrics or ():
        for scope_metrics in resource_metrics.scope_metrics:
            for metric in scope_metrics.metrics:
                points = metric.data.data_points if metric.data is not None else ()
                for point in points:
                    key = (metric.name, tuple(sorted((point.attributes or {}).items())))
                    if isinstance(point, HistogramDataPoint):
                        snapshot[key] = (float(point.count), point.sum)
                    elif isinstance(point, NumberDataPoint):
                        snapshot[key] = (float(point.value), None)
    return snapshot


def metric_delta(
    reader: InMemoryMetricReader, before: Snapshot, name: str, **attributes: str
) -> tuple[float, float | None]:
    """Приращение ряда (имя, метки) между срезами после действия в тесте.

    Ряд, которого не было в срезе `before` (темпоральность читателя — дельта:
    после каждого сбора значения обнуляются), считается с нуля — и по счёту,
    и по сумме.
    """
    key = (name, tuple(sorted(attributes.items())))
    count_before, sum_before = before.get(key, (0.0, 0.0))
    count_after, sum_after = metric_snapshot(reader).get(key, (0.0, 0.0))
    if sum_after is None or sum_before is None:
        return count_after - count_before, None
    return count_after - count_before, sum_after - sum_before


def metric_histogram(
    reader: InMemoryMetricReader, name: str, **attributes: str
) -> tuple[float, tuple[float, ...], tuple[int, ...]]:
    """Ряд гистограммы: (наблюдений, границы бакетов, счётчики бакетов).

    Границы нужны тестам бакетов (View с явными границами), счётчики —
    проверки «значение попало в свой бакет». Ряда нет — (0, (), ()).
    """
    data = reader.get_metrics_data()
    if data is None:
        return 0.0, (), ()
    wanted = tuple(sorted(attributes.items()))
    for resource_metrics in data.resource_metrics or ():
        for scope_metrics in resource_metrics.scope_metrics:
            for metric in scope_metrics.metrics:
                if metric.name != name:
                    continue
                points = metric.data.data_points if metric.data is not None else ()
                for point in points:
                    if not isinstance(point, HistogramDataPoint):
                        continue
                    if tuple(sorted((point.attributes or {}).items())) != wanted:
                        continue
                    counts = tuple(int(count) for count in point.bucket_counts)
                    return float(point.count), tuple(point.explicit_bounds), counts
    return 0.0, (), ()
