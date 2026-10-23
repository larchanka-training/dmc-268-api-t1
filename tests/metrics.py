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
    """Приращение ряда (имя, метки) между срезами после действия в тесте."""
    key = (name, tuple(sorted(attributes.items())))
    value_before, sum_before = before.get(key, (0.0, None))
    value_after, sum_after = metric_snapshot(reader).get(key, (0.0, None))
    if sum_after is None:
        return value_after - value_before, None
    assert sum_before is not None
    return value_after - value_before, sum_after - sum_before
