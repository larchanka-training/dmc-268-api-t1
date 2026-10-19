"""Фейковый `OAuthProvider`: профиль или сбой задаёт тест."""

from app.domain.entities import OAuthProfile
from app.domain.enums import Provider
from app.domain.login import OAuthProviderUnavailable

OCTOCAT = OAuthProfile(
    provider_user_id="583231",
    login="octocat",
    name="The Octocat",
    avatar_url="https://avatars.githubusercontent.com/u/583231",
)


class FakeOAuthProvider:
    provider = Provider.GITHUB

    def __init__(self, profile: OAuthProfile = OCTOCAT) -> None:
        self.profile = profile
        self.unavailable = False
        self.exchanged: list[str] = []

    def authorize_url(self, state: str) -> str:
        return f"https://github.com/login/oauth/authorize?state={state}"

    def exchange_code(self, code: str) -> OAuthProfile:
        self.exchanged.append(code)
        if self.unavailable:
            raise OAuthProviderUnavailable("github не ответил")
        return self.profile
