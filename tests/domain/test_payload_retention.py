"""Правило «дифф прогона удалён по сроку хранения» (change `diff-retention-visibility`, D1)."""

from datetime import UTC, datetime

from app.domain.payload_retention import BodyExpiryStats, diff_expired_at

LAST_EXPIRY = datetime(2026, 11, 6, 3, 0, tzinfo=UTC)


def test_all_bodies_erased_gives_last_expiry() -> None:
    stats = BodyExpiryStats(payload_count=3, erased_count=3, last_expired_at=LAST_EXPIRY)

    assert diff_expired_at(stats) == LAST_EXPIRY


def test_some_bodies_alive_gives_none() -> None:
    stats = BodyExpiryStats(payload_count=3, erased_count=2, last_expired_at=LAST_EXPIRY)

    assert diff_expired_at(stats) is None


def test_run_without_payloads_gives_none() -> None:
    stats = BodyExpiryStats(payload_count=0, erased_count=0, last_expired_at=None)

    assert diff_expired_at(stats) is None
