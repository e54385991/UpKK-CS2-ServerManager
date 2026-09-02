"""Health payload includes Python / FastAPI versions for the console footer."""

from fastapi.testclient import TestClient

from api.application import create_app
from api.metadata import APP_VERSION, BUILD_COMMIT, BUILD_TIME, _build_time, _short_commit


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
    assert body["git_sha"] == BUILD_COMMIT
    assert body["build_time"] == BUILD_TIME


def test_build_time_requires_timezone_and_normalizes_to_utc():
    assert _build_time("2026-09-02T03:04:05+08:00") == "2026-09-01T19:04:05Z"
    assert _build_time("2026-09-02T03:04:05") == "unknown"
    assert _build_time("not-a-timestamp") == "unknown"


def test_commit_output_is_limited_to_a_short_hex_prefix():
    assert _short_commit("ABCDEF1234567890") == "abcdef1"
    assert _short_commit("not-a-sha") == "unknown"
    assert _short_commit("short") == "unknown"
