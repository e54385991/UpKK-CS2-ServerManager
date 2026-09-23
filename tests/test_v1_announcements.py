"""Announcement feed authorization, validation, and route presentation."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from api.application import create_app
from modules import get_current_active_user, get_current_admin_user, get_db
from services.announcements.types import AnnouncementRecord

BASE = "/api/v1/announcements"
NOW = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)


def make_announcement(*, announcement_id: int = 1, published: bool = True) -> AnnouncementRecord:
    return AnnouncementRecord(
        id=announcement_id,
        title="Maintenance notice",
        body_markdown="**Scheduled maintenance**",
        is_published=published,
        created_at=NOW,
        updated_at=NOW,
        published_at=NOW if published else None,
    )


@pytest.fixture
def client(monkeypatch):
    app = create_app(lifespan=None)
    user = SimpleNamespace(id=7, username="admin", is_admin=True, is_active=True)

    async def database():
        yield SimpleNamespace()

    def active_user():
        if not user.is_active:
            raise HTTPException(status_code=403, detail="Inactive user")
        return user

    def admin_user():
        if not user.is_active or not user.is_admin:
            raise HTTPException(status_code=403, detail="Administrator required")
        return user

    app.dependency_overrides[get_db] = database
    app.dependency_overrides[get_current_active_user] = active_user
    app.dependency_overrides[get_current_admin_user] = admin_user
    monkeypatch.setattr(
        "api.routes.v1.announcements.list_published_announcements",
        AsyncMock(return_value=[make_announcement()]),
    )
    monkeypatch.setattr(
        "api.routes.v1.announcements.list_admin_announcements",
        AsyncMock(
            return_value=[
                make_announcement(),
                make_announcement(announcement_id=2, published=False),
            ]
        ),
    )
    monkeypatch.setattr(
        "api.routes.v1.announcements.create_announcement",
        AsyncMock(return_value=make_announcement()),
    )
    monkeypatch.setattr(
        "api.routes.v1.announcements.update_announcement",
        AsyncMock(return_value=make_announcement()),
    )
    monkeypatch.setattr(
        "api.routes.v1.announcements.delete_announcement",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr("api.routes.v1.announcements.record_audit_event", AsyncMock())
    return TestClient(app), user


def test_published_feed_and_admin_draft_list(client):
    api, user = client

    public = api.get(BASE)
    assert public.status_code == 200
    assert [item["id"] for item in public.json()["items"]] == [1]
    assert public.json()["items"][0]["body_markdown"] == "**Scheduled maintenance**"

    admin = api.get(f"{BASE}/admin")
    assert admin.status_code == 200
    assert [item["is_published"] for item in admin.json()["items"]] == [True, False]

    user.is_admin = False
    assert api.get(BASE).status_code == 200
    assert api.get(f"{BASE}/admin").status_code == 403
    assert api.post(BASE, json={"title": "Notice", "body_markdown": "Text"}).status_code == 403


def test_admin_create_update_delete_and_input_validation(client):
    api, _ = client
    body = {
        "title": "  Planned update  ",
        "body_markdown": "## Changes\n\n- Faster deploys",
        "is_published": True,
    }

    created = api.post(BASE, json=body)
    assert created.status_code == 201
    assert created.json()["title"] == "Maintenance notice"
    assert api.put(f"{BASE}/1", json=body).status_code == 200
    assert api.delete(f"{BASE}/1").json()["success"] is True

    assert api.post(BASE, json={**body, "extra": "rejected"}).status_code == 422
    assert api.post(BASE, json={**body, "title": "   "}).status_code == 422
    assert api.post(BASE, json={**body, "body_markdown": " "}).status_code == 422
    assert api.post(BASE, json={**body, "body_markdown": "x" * 20001}).status_code == 422

    from api.routes.v1 import announcements as announcement_routes

    announcement_routes.update_announcement.return_value = None
    announcement_routes.delete_announcement.return_value = False
    assert api.put(f"{BASE}/404", json=body).status_code == 404
    assert api.delete(f"{BASE}/404").status_code == 404
