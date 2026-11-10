"""Адаптер симуляций песочницы на PostgreSQL: ограничения, гонка запусков, результат."""

import datetime as dt
import threading
import time
from dataclasses import replace

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.domain.entities import (
    DiffAnchor,
    DiscardedFinding,
    LlmFinding,
    LlmRuleRef,
    RuleRef,
    Simulation,
    SimulationFinding,
    SimulationResult,
    User,
    Verdict,
)
from app.domain.enums import (
    DiffSide,
    DiscardStage,
    FindingCategory,
    FindingSeverity,
    Provider,
    SimulationSourceKind,
    SimulationStatus,
    VerificationOutcome,
    VerifierVerdict,
)
from app.domain.file_context import ContextOmission, ContextRule, FileContext
from app.domain.ids import new_id
from app.domain.secret_redaction import REDACTED
from app.domain.simulation import (
    SIMULATION_STALE,
    advance_simulation,
    complete_simulation,
    fail_simulation,
    new_simulation,
)
from app.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork

from ..conftest import requires_db

pytestmark = [pytest.mark.integration, requires_db]

NOW = dt.datetime(2026, 11, 1, 12, 0, tzinfo=dt.UTC)
LATER = NOW + dt.timedelta(seconds=40)
TOKEN = "ghp_" + "a1B2" * 9


@pytest.fixture
def uow(clean_db):
    return SqlAlchemyUnitOfWork(clean_db)


@pytest.fixture
def user(uow) -> User:
    stored = User(
        id=new_id(),
        provider=Provider.GITHUB,
        provider_user_id="583231",
        login="octocat",
        name=None,
        avatar_url=None,
        created_at=NOW,
        updated_at=NOW,
    )
    with uow as work:
        work.users.add(stored)
        work.commit()
    return stored


def a_simulation(user: User, *, at: dt.datetime = NOW) -> Simulation:
    return new_simulation(
        simulation_id=new_id(),
        user_id=user.id,
        source_kind=SimulationSourceKind.CODE,
        source=f'TOKEN = "{TOKEN}"\n',
        rules="## Secrets\n\n- [ ] no tokens in code\n",
        file_path="app/settings.py",
        now=at,
        ttl=dt.timedelta(days=1),
    )


def a_result() -> SimulationResult:
    anchor = DiffAnchor("app/settings.py", DiffSide.NEW, new_line=1)
    return SimulationResult(
        findings=(
            SimulationFinding(
                id=new_id(),
                anchor=anchor,
                category=FindingCategory.SECURITY,
                severity=FindingSeverity.CRITICAL,
                message=f"Токен {TOKEN} зашит в код",
                suggestion="Читать из окружения",
                confidence=0.9,
                rule=RuleRef("Secrets", 1, "no tokens in code"),
                verdict=Verdict(VerifierVerdict.DROP, "похоже на тестовый токен"),
            ),
        ),
        discarded=(
            DiscardedFinding(
                stage=DiscardStage.SCREENING,
                reason="привязка вне показанного диффа",
                candidate=LlmFinding(
                    anchor=DiffAnchor("app/settings.py", DiffSide.NEW, new_line=40),
                    category=FindingCategory.READABILITY,
                    severity=FindingSeverity.LOW,
                    message="Имя константы",
                    rule=LlmRuleRef("Naming", 3),
                ),
            ),
            DiscardedFinding(
                stage=DiscardStage.VERIFICATION,
                reason="стиль, не ошибка",
                candidate=LlmFinding(
                    anchor=anchor,
                    category=FindingCategory.READABILITY,
                    severity=FindingSeverity.LOW,
                    message="Кавычки",
                    confidence=0.3,
                ),
            ),
        ),
        context=(
            FileContext(
                file_path="app/settings.py",
                hunks=(f'@@ -0,0 +1 @@\n+TOKEN = "{TOKEN}"',),
                scopes=None,
                imports="import os",
                rules=(ContextRule("Secrets", ("no tokens in code",)),),
                omitted=(ContextOmission("ast", "too_large"),),
            ),
        ),
    )


def _complete(uow, simulation: Simulation, result: SimulationResult) -> Simulation:
    """Пройти этапы, как воркер: каждый переход - своя запись, адаптер сверяет его с сохранённым."""
    for status in (SimulationStatus.BUILDING_CONTEXT, SimulationStatus.ANALYSING, SimulationStatus.VERIFYING):
        simulation = advance_simulation(simulation, status, LATER).unwrap()
        with uow as work:
            assert work.simulations.update(simulation) is True
            work.commit()
    done = complete_simulation(
        simulation,
        result,
        model="glm-5.3",
        tokens_used=1500,
        verification=VerificationOutcome.VERIFIED,
        now=LATER,
    ).unwrap()
    with uow as work:
        assert work.simulations.update(done) is True
        work.commit()
    return done


def test_a_simulation_is_read_back_only_by_its_author(uow, user) -> None:
    simulation = a_simulation(user)
    with uow as work:
        assert work.simulations.add(simulation) is True
        work.commit()
    with uow as work:
        assert work.simulations.get(simulation.id) == simulation
        assert work.simulations.get_for_user(simulation.id, user.id) == simulation
        assert work.simulations.get_for_user(simulation.id, new_id()) is None


def test_a_second_unfinished_simulation_is_not_added(uow, user) -> None:
    first, second = a_simulation(user), a_simulation(user)
    with uow as work:
        work.simulations.add(first)
        work.commit()
    with uow as work:
        assert work.simulations.has_active(user.id) is True
        assert work.simulations.add(second) is False
        work.commit()
    with uow as work:
        work.simulations.update(fail_simulation(first, "LLM_TIMEOUT", LATER).unwrap())
        work.commit()
    with uow as work:
        assert work.simulations.has_active(user.id) is False
        assert work.simulations.add(second) is True
        work.commit()


def test_two_concurrent_launches_leave_one_unfinished_simulation(clean_db, user) -> None:
    first, second = a_simulation(user), a_simulation(user)
    inserted = threading.Event()
    results: list[bool] = []

    def late_launch() -> None:
        if not inserted.wait(timeout=10):
            return
        with SqlAlchemyUnitOfWork(clean_db) as work:
            # Ждёт коммита первой транзакции и затем видит её строку в индексе.
            results.append(work.simulations.add(second))
            work.commit()

    # daemon и таймаут ожидания: упавшая первая вставка не вешает прогон.
    thread = threading.Thread(target=late_launch, daemon=True)
    thread.start()
    with SqlAlchemyUnitOfWork(clean_db) as work:
        assert work.simulations.add(first) is True
        inserted.set()
        time.sleep(0.3)
        work.commit()
    thread.join(timeout=10)

    assert results == [False]
    with SqlAlchemyUnitOfWork(clean_db) as work:
        assert work.simulations.get(second.id) is None


def test_the_result_round_trips_with_secrets_hidden_only_in_the_context(uow, user) -> None:
    simulation = a_simulation(user)
    result = a_result()
    with uow as work:
        work.simulations.add(simulation)
        work.commit()
    done = _complete(uow, simulation, result)
    with uow as work:
        stored = work.simulations.get(simulation.id)

    assert stored is not None
    (context,) = result.context
    expected = replace(
        result,
        context=(replace(context, hunks=(f'@@ -0,0 +1 @@\n+TOKEN = "{REDACTED}"',)),),
    )
    assert stored == replace(done, result=expected)
    # Замечания пишутся как есть - так же, как находки прогона.
    assert stored.result is not None
    assert TOKEN in stored.result.findings[0].message


def test_a_finished_row_keeps_no_user_input(uow, user, clean_db) -> None:
    simulation = a_simulation(user)
    with uow as work:
        work.simulations.add(simulation)
        work.commit()
    _complete(uow, simulation, a_result())
    with clean_db.connect() as connection:
        row = connection.execute(text("SELECT source, rules, file_path FROM simulations")).one()
        dump = connection.execute(text("SELECT simulations::text FROM simulations")).scalar_one()
    assert row == (None, None, "app/settings.py")
    assert "no tokens in code\n" not in dump


def test_an_update_after_the_simulation_was_closed_writes_nothing(uow, user) -> None:
    """Воркер дописывает итог, а симуляцию уже закрыли как зависшую."""
    simulation = a_simulation(user)
    with uow as work:
        work.simulations.add(simulation)
        work.commit()
    analysing = advance_simulation(simulation, SimulationStatus.BUILDING_CONTEXT, NOW).unwrap()
    with uow as work:
        work.simulations.update(analysing)
        work.commit()
    with uow as work:
        assert work.simulations.fail_stale(NOW + dt.timedelta(seconds=1), LATER) == 1
        work.commit()
    late = advance_simulation(analysing, SimulationStatus.ANALYSING, LATER).unwrap()
    with uow as work:
        assert work.simulations.update(late) is False
        work.commit()
    with uow as work:
        stored = work.simulations.get(simulation.id)
    assert stored is not None
    assert (stored.status, stored.error_code, stored.result) == (SimulationStatus.FAILED, SIMULATION_STALE, None)


def test_a_skipped_step_and_a_missing_row_are_refused(uow, user) -> None:
    simulation = a_simulation(user)
    with uow as work:
        work.simulations.add(simulation)
        work.commit()
    with uow as work, pytest.raises(ValueError):
        work.simulations.update(replace(simulation, status=SimulationStatus.VERIFYING))
    with uow as work, pytest.raises(LookupError):
        work.simulations.update(a_simulation(user))


def test_stale_simulations_fail_with_their_input_erased(uow, user, clean_db) -> None:
    stale = a_simulation(user)
    with uow as work:
        work.simulations.add(stale)
        work.commit()
    other = User(
        id=new_id(),
        provider=Provider.GITHUB,
        provider_user_id="1",
        login="other",
        name=None,
        avatar_url=None,
        created_at=NOW,
        updated_at=NOW,
    )
    fresh = a_simulation(other, at=LATER)
    with uow as work:
        work.users.add(other)
        work.simulations.add(fresh)
        work.commit()

    with uow as work:
        assert work.simulations.fail_stale(LATER, LATER + dt.timedelta(minutes=10)) == 1
        work.commit()

    with uow as work:
        failed = work.simulations.get(stale.id)
        untouched = work.simulations.get(fresh.id)
    assert failed is not None
    assert (failed.status, failed.error_code) == (SimulationStatus.FAILED, SIMULATION_STALE)
    assert (failed.source, failed.rules) == (None, None)
    assert failed.updated_at == LATER + dt.timedelta(minutes=10)
    assert untouched == fresh


def test_launches_since_a_moment_are_listed_in_order(uow, user) -> None:
    times = [NOW - dt.timedelta(minutes=m) for m in (90, 50, 10)]
    with uow as work:
        for at in times:
            work.simulations.add(fail_simulation(a_simulation(user, at=at), "LLM_TIMEOUT", at).unwrap())
        work.commit()
    with uow as work:
        assert work.simulations.created_since(user.id, NOW - dt.timedelta(hours=1)) == times[1:]
        assert work.simulations.created_since(new_id(), NOW - dt.timedelta(hours=1)) == []


def test_expired_simulations_are_deleted(uow, user) -> None:
    expired = fail_simulation(a_simulation(user, at=NOW - dt.timedelta(days=2)), "LLM_TIMEOUT", NOW).unwrap()
    live = a_simulation(user)
    with uow as work:
        work.simulations.add(expired)
        work.simulations.add(live)
        work.commit()
    with uow as work:
        assert work.simulations.delete_expired(NOW) == 1
        work.commit()
    with uow as work:
        assert work.simulations.get(expired.id) is None
        assert work.simulations.get(live.id) == live


def test_deleting_a_user_deletes_their_simulations(uow, user, clean_db) -> None:
    simulation = a_simulation(user)
    with uow as work:
        work.simulations.add(simulation)
        work.commit()
    with clean_db.begin() as connection:
        connection.execute(text("DELETE FROM users"))
    with uow as work:
        assert work.simulations.get(simulation.id) is None


@pytest.mark.parametrize(
    "assignment",
    [
        # Итоговая строка со входом пользователя (design D10).
        "status = 'failed', error_code = 'X'",
        "status = 'completed', result = '{}', source = NULL",
        # Фрагмент без пути и дифф с путём.
        "file_path = NULL",
        "source_kind = 'diff'",
        # Итог без результата или без кода сбоя.
        "status = 'completed', source = NULL, rules = NULL",
        "status = 'failed', source = NULL, rules = NULL",
        "tokens_used = -1",
    ],
)
def test_the_database_refuses_inconsistent_rows(uow, user, clean_db, assignment: str) -> None:
    simulation = a_simulation(user)
    with uow as work:
        work.simulations.add(simulation)
        work.commit()
    with pytest.raises(IntegrityError), clean_db.begin() as connection:
        connection.execute(text(f"UPDATE simulations SET {assignment}"))
