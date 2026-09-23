import "server-only";
import { apiFetch, type ApiResult } from "@/shared/api/server-fetch";
import type {
  ActionResultDto,
  AnnouncementFeedViewDto,
  AnnouncementListViewDto,
  AnnouncementViewDto,
} from "@/shared/api/types";
import type {
  Announcement,
  AnnouncementWrite,
  CS2UpdateNotice,
  PublishedAnnouncementFeed,
} from "@/modules/announcements/types";

function toAnnouncement(raw: AnnouncementViewDto): Announcement {
  return {
    id: raw.id,
    title: raw.title,
    bodyMarkdown: raw.body_markdown,
    isPublished: raw.is_published,
    createdAt: raw.created_at,
    updatedAt: raw.updated_at,
    publishedAt: raw.published_at ?? null,
  };
}

function toCS2UpdateNotice(
  raw: AnnouncementFeedViewDto["cs2_update_notice"],
): CS2UpdateNotice | null {
  return raw
    ? {
        version: raw.version,
        changedAt: raw.changed_at,
        expiresAt: raw.expires_at,
      }
    : null;
}

export async function getPublishedAnnouncements(): Promise<
  ApiResult<PublishedAnnouncementFeed>
> {
  const result = await apiFetch<AnnouncementFeedViewDto>("/api/v1/announcements");
  if (!result.ok) return result;
  return {
    ok: true,
    data: {
      items: result.data.items.map(toAnnouncement),
      cs2UpdateNotice: toCS2UpdateNotice(result.data.cs2_update_notice),
    },
  };
}

export async function getAdminAnnouncements(): Promise<
  ApiResult<Announcement[]>
> {
  const result = await apiFetch<AnnouncementListViewDto>(
    "/api/v1/announcements/admin",
  );
  if (!result.ok) return result;
  return { ok: true, data: result.data.items.map(toAnnouncement) };
}

export async function saveAnnouncement(
  id: number | null,
  body: AnnouncementWrite,
): Promise<ApiResult<Announcement>> {
  const result = await apiFetch<AnnouncementViewDto>(
    id === null ? "/api/v1/announcements" : `/api/v1/announcements/${id}`,
    {
      method: id === null ? "POST" : "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        title: body.title,
        body_markdown: body.bodyMarkdown,
        is_published: body.isPublished,
      }),
    },
  );
  if (!result.ok) return result;
  return { ok: true, data: toAnnouncement(result.data) };
}

export async function removeAnnouncement(
  id: number,
): Promise<ApiResult<ActionResultDto>> {
  return apiFetch<ActionResultDto>(`/api/v1/announcements/${id}`, {
    method: "DELETE",
  });
}
