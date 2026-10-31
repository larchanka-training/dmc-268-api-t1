"""Ссылка на запрос на изменение у провайдера."""

from app.domain.enums import Provider
from app.domain.provider_links import app_install_url, merge_request_url


def test_github_link_points_to_the_pull_request_page() -> None:
    assert (
        merge_request_url(Provider.GITHUB, "octo-org/api", 42)
        == "https://github.com/octo-org/api/pull/42"
    )


def test_a_provider_without_a_known_link_format_gets_no_link() -> None:
    assert merge_request_url(Provider.GITLAB, "octo-org/api", 42) is None


def test_install_url_points_to_the_app_installation_screen_with_state() -> None:
    assert app_install_url("review-bot", "abc") == "https://github.com/apps/review-bot/installations/new?state=abc"
