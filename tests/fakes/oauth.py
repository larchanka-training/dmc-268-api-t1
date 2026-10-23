"""Фейковый `OAuthProvider`: профиль, токен, исход refresh или сбой задаёт тест."""

from app.domain.entities import OAuthLogin, OAuthProfile, ProviderGrant, Secret
from app.domain.enums import Provider
from app.domain.login import OAuthProviderUnavailable
from app.domain.provider_token import ProviderTokenRejected

OCTOCAT = OAuthProfile(
    provider_user_id="583231",
    login="octocat",
    name="The Octocat",
    avatar_url="https://avatars.githubusercontent.com/u/583231",
)

# Токены с истечением: так GitHub отвечает, если у App включено истечение
# user access token (8 часов, refresh token — 6 месяцев).
EXPIRING_GRANT = ProviderGrant(
    access_token=Secret("ghu_login_access"),
    expires_in=28_800,
    refresh_token=Secret("ghr_login_refresh"),
    refresh_token_expires_in=15_897_600,
)
REFRESHED_GRANT = ProviderGrant(
    access_token=Secret("ghu_refreshed_access"),
    expires_in=28_800,
    refresh_token=Secret("ghr_refreshed_refresh"),
    refresh_token_expires_in=15_897_600,
)


class FakeOAuthProvider:
    provider = Provider.GITHUB

    def __init__(
        self,
        profile: OAuthProfile = OCTOCAT,
        grant: ProviderGrant = EXPIRING_GRANT,
        refreshed: ProviderGrant = REFRESHED_GRANT,
    ) -> None:
        self.profile = profile
        self.grant = grant
        self.refreshed = refreshed
        self.unavailable = False
        # Исход refresh: `None` — выдать `refreshed`, иначе бросить это исключение.
        self.refresh_error: Exception | None = None
        self.revoke_error: Exception | None = None
        self.exchanged: list[str] = []
        self.refreshes: list[Secret] = []
        self.revoked: list[Secret] = []

    def authorize_url(self, state: str) -> str:
        return f"https://github.com/login/oauth/authorize?state={state}"

    def exchange_code(self, code: str) -> OAuthLogin:
        self.exchanged.append(code)
        if self.unavailable:
            raise OAuthProviderUnavailable("github не ответил")
        return OAuthLogin(profile=self.profile, grant=self.grant)

    def refresh(self, refresh_token: Secret) -> ProviderGrant:
        self.refreshes.append(refresh_token)
        if self.refresh_error is not None:
            raise self.refresh_error
        return self.refreshed

    def revoke(self, access_token: Secret) -> None:
        self.revoked.append(access_token)
        if self.revoke_error is not None:
            raise self.revoke_error


def rejecting() -> ProviderTokenRejected:
    return ProviderTokenRejected("bad_refresh_token")
