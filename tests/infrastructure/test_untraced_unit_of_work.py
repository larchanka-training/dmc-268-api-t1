"""Единица работы опроса потока событий не пишет спанов SQL. SQLite в памяти вместо базы:
проверяется инструментирование движка, а не SQL."""

from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from sqlalchemy import create_engine, text

from app.infrastructure import telemetry
from app.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork, UntracedUnitOfWork


def _engine():
    engine = create_engine("sqlite://")
    telemetry.instrument_engine(engine)
    return engine


def test_the_plain_unit_of_work_traces_its_queries(spans: InMemorySpanExporter) -> None:
    """Контроль: инструментирование движка работает, иначе тест ниже ничего не докажет."""
    with SqlAlchemyUnitOfWork(_engine()) as uow:
        uow.session.execute(text("select 1"))

    assert len(spans.get_finished_spans()) == 1


def test_the_untraced_unit_of_work_writes_no_spans(spans: InMemorySpanExporter) -> None:
    engine = _engine()

    with UntracedUnitOfWork(engine) as uow:
        uow.session.execute(text("select 1"))

    assert spans.get_finished_spans() == ()
    # После выхода подавление снято: соседние запросы того же потока снова видны.
    with SqlAlchemyUnitOfWork(engine) as uow:
        uow.session.execute(text("select 1"))
    assert len(spans.get_finished_spans()) == 1
