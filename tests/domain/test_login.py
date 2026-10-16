import pytest

from app.domain.login import DEFAULT_REDIRECT, safe_redirect


@pytest.mark.parametrize(
    "value",
    ["/app/", "/app/reviews/42", "/app/reviews/42?tab=findings#f-3", "/app/repos/a.b"],
)
def test_a_path_under_app_is_kept(value: str) -> None:
    assert safe_redirect(value) == value


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        "/app",
        "/login",
        "/api/v1/auth/me",
        "https://evil.example/",
        "//evil.example/app/",
        "/app//evil.example",
        "/app/\\evil.example",
        "/app/../api/v1/auth/logout",
        "/app/%2e%2e/login",
        "/app/x\r\nSet-Cookie: a=b",
        "app/reviews",
    ],
)
def test_anything_else_falls_back_to_app(value: str | None) -> None:
    assert safe_redirect(value) == DEFAULT_REDIRECT == "/app/"
