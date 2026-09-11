from app.main import health_check


def test_health_check_returns_ok():
    assert health_check() == {"status": "ok"}
