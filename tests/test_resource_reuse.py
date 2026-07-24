"""Regression coverage for shared HTTP and S3 client lifecycles."""

from __future__ import annotations

import httpx
import pytest

import modules.http_helper as http_helper_module
from api.routes import auth
from cs2_manager.core import Principal
from modules.http_helper import HTTPHelper
from modules.models import User
from modules.schemas import S3SettingsUpdate
from services.discord_notification_service import DiscordNotificationService
from services.s3_backup_service import S3BackupService


def make_s3_user(user_id: int, *, secret: str = "super-secret") -> User:
    return User(
        id=user_id,
        username=f"user-{user_id}",
        email=f"user-{user_id}@example.com",
        hashed_password="hash",
        s3_enabled=True,
        s3_endpoint_url="https://s3.example.com",
        s3_region="test-1",
        s3_bucket="backups",
        s3_access_key_id="access-key",
        s3_secret_access_key=secret,
        s3_prefix="cs2",
        s3_use_ssl=True,
    )


class FakeS3Client:
    def __init__(self, user_id: int) -> None:
        self.user_id = user_id
        self.close_calls = 0

    def close(self) -> None:
        self.close_calls += 1


@pytest.mark.asyncio
async def test_http_helper_raw_requests_share_one_lifespan_client(monkeypatch):
    class SharedClient:
        def __init__(self) -> None:
            self.is_closed = False
            self.requests = []
            self.close_calls = 0

        async def request(self, method: str, url: str, **kwargs):
            self.requests.append((method, url, kwargs))
            return httpx.Response(200, json={"ok": True})

        async def aclose(self) -> None:
            self.close_calls += 1
            self.is_closed = True

    shared_client = SharedClient()
    factory_calls = []

    def client_factory(**kwargs):
        factory_calls.append(kwargs)
        return shared_client

    monkeypatch.setattr(http_helper_module.httpx, "AsyncClient", client_factory)
    helper = HTTPHelper()

    await helper.request("GET", "https://api.example.com/one")
    async with helper.borrow_client() as borrowed:
        assert borrowed is shared_client
    await helper.request("POST", "https://api.example.com/two", json={"value": 2})

    assert len(factory_calls) == 1
    assert len(shared_client.requests) == 2
    await helper.close()
    assert shared_client.close_calls == 1


@pytest.mark.asyncio
async def test_s3_cache_reuses_clients_and_never_places_secrets_in_keys(monkeypatch):
    service = S3BackupService(max_cached_clients=2)
    created: list[FakeS3Client] = []

    def create_client(user: User) -> FakeS3Client:
        client = FakeS3Client(int(user.id))
        created.append(client)
        return client

    monkeypatch.setattr(service, "_get_client", create_client)
    user = make_s3_user(1)

    async with service._client_lease(user) as first:
        pass
    async with service._client_lease(user) as second:
        pass

    assert first is second
    assert len(created) == 1
    assert service.cached_client_count == 1
    cache_keys = repr(tuple(service._client_cache.keys()))
    assert "super-secret" not in cache_keys
    assert "access-key" not in cache_keys

    await service.close()
    assert first.close_calls == 1


@pytest.mark.asyncio
async def test_s3_cache_is_bounded_and_closes_lru_clients(monkeypatch):
    service = S3BackupService(max_cached_clients=2)
    created: list[FakeS3Client] = []

    def create_client(user: User) -> FakeS3Client:
        client = FakeS3Client(int(user.id))
        created.append(client)
        return client

    monkeypatch.setattr(service, "_get_client", create_client)

    for user_id in (1, 2, 3):
        async with service._client_lease(make_s3_user(user_id)):
            pass

    assert service.cached_client_count == 2
    assert created[0].close_calls == 1
    assert created[1].close_calls == 0
    assert created[2].close_calls == 0

    await service.close()
    assert [client.close_calls for client in created] == [1, 1, 1]


@pytest.mark.asyncio
async def test_s3_invalidation_defers_close_until_active_lease_finishes(monkeypatch):
    service = S3BackupService()
    client = FakeS3Client(7)
    monkeypatch.setattr(service, "_get_client", lambda _user: client)
    user = make_s3_user(7)

    async with service._client_lease(user):
        assert await service.invalidate_user(user.id) == 1
        assert service.cached_client_count == 0
        assert client.close_calls == 0

    assert client.close_calls == 1


@pytest.mark.asyncio
async def test_s3_configuration_change_replaces_and_closes_old_client(monkeypatch):
    service = S3BackupService()
    created: list[FakeS3Client] = []

    def create_client(user: User) -> FakeS3Client:
        client = FakeS3Client(int(user.id))
        created.append(client)
        return client

    monkeypatch.setattr(service, "_get_client", create_client)
    user = make_s3_user(11, secret="old-secret")

    async with service._client_lease(user):
        pass
    user.s3_secret_access_key = "new-secret"
    async with service._client_lease(user):
        pass

    assert len(created) == 2
    assert created[0].close_calls == 1
    assert created[1].close_calls == 0
    await service.close()


@pytest.mark.asyncio
async def test_discord_posts_through_injected_shared_http_client():
    class SharedHTTP:
        def __init__(self) -> None:
            self.calls = []

        async def request(self, method: str, url: str, **kwargs):
            self.calls.append((method, url, kwargs))
            return httpx.Response(204)

    shared_http = SharedHTTP()
    service = DiscordNotificationService(http_client=shared_http)  # type: ignore[arg-type]

    assert await service._post_payload(
        "https://discord.com/api/webhooks/123/token",
        {"content": "first"},
    ) == (True, None)
    assert await service._post_payload(
        "https://discord.com/api/webhooks/123/token",
        {"content": "second"},
    ) == (True, None)

    assert len(shared_http.calls) == 2
    assert all(call[0] == "POST" for call in shared_http.calls)
    assert all(call[2]["follow_redirects"] is False for call in shared_http.calls)


@pytest.mark.asyncio
async def test_s3_settings_update_invalidates_cache_after_commit(monkeypatch):
    events: list[str] = []

    class Database:
        def __init__(self, user: User) -> None:
            self.user = user

        async def get(self, _model, _user_id: int) -> User:
            return self.user

        async def commit(self) -> None:
            events.append("commit")

    class TestUow:
        def __init__(self, session: Database) -> None:
            self.session = session

        async def commit(self) -> None:
            await self.session.commit()

    async def validate_captcha(_token: str, _code: str) -> bool:
        return True

    async def invalidate_user(user_id: int | None) -> int:
        events.append(f"invalidate:{user_id}")
        return 1

    monkeypatch.setattr(auth.captcha_service, "validate_captcha", validate_captcha)
    monkeypatch.setattr(auth.s3_backup_service, "invalidate_user", invalidate_user)
    user = make_s3_user(42)
    update = S3SettingsUpdate(
        bucket="new-backups",
        captcha_token="token",
        captcha_code="1234",
    )

    response = await auth.update_s3_settings(
        update,
        Principal.model_validate(user),
        TestUow(Database(user)),  # type: ignore[arg-type]
        auth.s3_backup_service,
    )

    assert response.bucket == "new-backups"
    assert events == ["commit", "invalidate:42"]
