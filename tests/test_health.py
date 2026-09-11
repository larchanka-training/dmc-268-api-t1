from fastapi.testclient import TestClient

from app.api.factory import create_app
from app.config import Settings

client = TestClient(create_app(Settings(database_url="postgresql+psycopg://test/test")))


def test_root() -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert response.json() == {"message": "Welcome to DMC-268 Team 1 API"}


def test_health() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
