"""Health payload includes Python / FastAPI versions for the console footer."""

from fastapi.testclient import TestClient

from api.application import create_app
from api.metadata import APP_VERSION


def test_health_includes_runtime_versions():
    client = TestClient(create_app(lifespan=None))
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "healthy"
    assert body["version"] == APP_VERSION
    assert body["python"].count(".") >= 1
    assert body["fastapi"]
    assert body["fastapi"] != "unknown"
