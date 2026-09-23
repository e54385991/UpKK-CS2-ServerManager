"""Published announcement feed and administrator management endpoints."""

from fastapi import APIRouter, HTTPException, Request, status

from api.contracts.v1.announcements import (
    AnnouncementListView,
    AnnouncementView,
    AnnouncementWrite,
)
from api.contracts.v1.settings import ActionResult
from api.dependencies import ActiveUser, AdminUser, DatabaseSession
from services.announcements.service import (
    create_announcement,
    delete_announcement,
    list_admin_announcements,
    list_published_announcements,
    update_announcement,
)
from services.announcements.types import AnnouncementRecord
from services.audit_log_service import record_audit_event

router = APIRouter(prefix="/api/v1/announcements", tags=["v1-announcements"])


def _view(item: AnnouncementRecord) -> AnnouncementView:
    return AnnouncementView(
        id=item.id,
        title=item.title,
        body_markdown=item.body_markdown,
        is_published=item.is_published,
        created_at=item.created_at,
        updated_at=item.updated_at,
        published_at=item.published_at,
    )


@router.get("", response_model=AnnouncementListView)
async def list_announcements(
    db: DatabaseSession,
    _current_user: ActiveUser,
) -> AnnouncementListView:
    """Return published announcements to every active console user."""
    items = await list_published_announcements(db)
    return AnnouncementListView(items=[_view(item) for item in items])


@router.get("/admin", response_model=AnnouncementListView)
async def list_admin_announcements_route(
    db: DatabaseSession,
    _current_user: AdminUser,
) -> AnnouncementListView:
    """Return drafts and published notices for administrators."""
    items = await list_admin_announcements(db)
    return AnnouncementListView(items=[_view(item) for item in items])


@router.post("", response_model=AnnouncementView, status_code=status.HTTP_201_CREATED)
async def create_announcement_route(
    body: AnnouncementWrite,
    db: DatabaseSession,
    current_user: AdminUser,
    request: Request,
) -> AnnouncementView:
    item = await create_announcement(
        db,
        title=body.title,
        body_markdown=body.body_markdown,
        is_published=body.is_published,
        actor_user_id=current_user.id,
    )
    await record_audit_event(
        category="settings",
        action="announcement.create",
        status="success",
        user=current_user,
        request=request,
        details={"announcement_id": item.id, "is_published": item.is_published},
    )
    return _view(item)


@router.put("/{announcement_id}", response_model=AnnouncementView)
async def update_announcement_route(
    announcement_id: int,
    body: AnnouncementWrite,
    db: DatabaseSession,
    current_user: AdminUser,
    request: Request,
) -> AnnouncementView:
    item = await update_announcement(
        db,
        announcement_id,
        title=body.title,
        body_markdown=body.body_markdown,
        is_published=body.is_published,
    )
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Announcement not found")
    await record_audit_event(
        category="settings",
        action="announcement.update",
        status="success",
        user=current_user,
        request=request,
        details={"announcement_id": item.id, "is_published": item.is_published},
    )
    return _view(item)


@router.delete("/{announcement_id}", response_model=ActionResult)
async def delete_announcement_route(
    announcement_id: int,
    db: DatabaseSession,
    current_user: AdminUser,
    request: Request,
) -> ActionResult:
    deleted = await delete_announcement(db, announcement_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Announcement not found")
    await record_audit_event(
        category="settings",
        action="announcement.delete",
        status="success",
        user=current_user,
        request=request,
        details={"announcement_id": announcement_id},
    )
    return ActionResult(success=True, message="Announcement deleted.")
