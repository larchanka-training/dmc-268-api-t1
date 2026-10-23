"""Сроки токена пользователя: когда обновлять и можно ли; секрет не раскрывается."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

from app.domain.entities import ProviderGrant, ProviderToken, Secret
from app.domain.provider_token import can_refresh, needs_refresh, token_from_grant

NOW = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)

EXPIRING = ProviderGrant(
    access_token=Secret("ghu_access"),
    expires_in=28_800,
    refresh_token=Secret("ghr_refresh"),
    refresh_token_expires_in=15_897_600,
)
# Ответ GitHub, если истечение токенов у App выключено: только access_token.
NON_EXPIRING = ProviderGrant(access_token=Secret("ghu_forever"), expires_in=None, refresh_token=None, refresh_token_expires_in=None)


def test_expiring_grant_gets_absolute_expiry() -> None:
    token = token_from_grant(EXPIRING, NOW)

    assert token == ProviderToken(
        access_token=Secret("ghu_access"),
        access_expires_at=datetime(2026, 10, 23, 20, 0, tzinfo=UTC),
        refresh_token=Secret("ghr_refresh"),
        refresh_expires_at=datetime(2027, 4, 25, 12, 0, tzinfo=UTC),
    )


def test_non_expiring_token_never_needs_refresh() -> None:
    token = token_from_grant(NON_EXPIRING, NOW)

    assert (token.access_expires_at, token.refresh_token, token.refresh_expires_at) == (None, None, None)
    assert not needs_refresh(token, NOW + timedelta(days=3650))


def test_refresh_is_due_five_minutes_before_expiry() -> None:
    token = token_from_grant(EXPIRING, NOW)
    expires = datetime(2026, 10, 23, 20, 0, tzinfo=UTC)

    assert not needs_refresh(token, expires - timedelta(minutes=5, seconds=1))
    assert needs_refresh(token, expires - timedelta(minutes=5))
    assert needs_refresh(token, expires + timedelta(hours=1))


def test_refresh_token_works_until_its_own_expiry() -> None:
    token = token_from_grant(EXPIRING, NOW)
    refresh_expires = datetime(2027, 4, 25, 12, 0, tzinfo=UTC)

    assert can_refresh(token, refresh_expires - timedelta(seconds=1))
    assert not can_refresh(token, refresh_expires)


def test_without_refresh_token_nothing_to_refresh() -> None:
    token = replace(token_from_grant(EXPIRING, NOW), refresh_token=None, refresh_expires_at=None)

    assert not can_refresh(token, NOW)


def test_refresh_token_without_expiry_is_refreshable() -> None:
    grant = replace(EXPIRING, refresh_token_expires_in=None)

    assert can_refresh(token_from_grant(grant, NOW), NOW + timedelta(days=3650))


def test_secret_never_shows_its_value() -> None:
    token = token_from_grant(EXPIRING, NOW)

    for text in (repr(token), str(token), f"{token.access_token}", repr(EXPIRING)):
        assert "ghu_access" not in text
        assert "ghr_refresh" not in text
    assert token.access_token.reveal() == "ghu_access"
