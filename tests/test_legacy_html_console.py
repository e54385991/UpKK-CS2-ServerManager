"""Old Jinja HTML paths are gone; FastAPI only serves JSON APIs and /static."""

from fastapi.testclient import TestClient

from api.application import create_app


def _client() -> TestClient:
    return TestClient(create_app(lifespan=None))


def test_legacy_html_paths_return_not_found():
    client = _client()
    for path in (
        "/",
        "/login",
        "/register",
        "/forgot-password",
        "/reset-password",
        "/servers-ui",
        "/servers-ui/4",
        "/plugin-market",
        "/profile",
        "/google-callback",
        "/deployment-tutorial",
    ):
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 404, path
        assert "location" not in response.headers


def test_api_and_health_remain_available():
    client = _client()
    health = client.get("/health")
    assert health.status_code == 200
    me = client.get("/api/v1/auth/me")
    assert me.status_code == 401
    assert "location" not in me.headers
