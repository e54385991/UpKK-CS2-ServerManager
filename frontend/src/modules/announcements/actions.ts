"use server";

import { revalidatePath } from "next/cache";
import type { ApiResult } from "@/shared/api/server-fetch";
import type { ActionResultDto } from "@/shared/api/types";
import {
  getAdminAnnouncements,
  removeAnnouncement,
  saveAnnouncement,
} from "@/modules/announcements/api";
import type { Announcement, AnnouncementWrite } from "@/modules/announcements/types";

export async function refreshAdminAnnouncementsAction(): Promise<
  ApiResult<Announcement[]>
> {
  return getAdminAnnouncements();
}

export async function saveAnnouncementAction(
  id: number | null,
  body: AnnouncementWrite,
): Promise<ApiResult<Announcement>> {
  const result = await saveAnnouncement(id, body);
  if (result.ok) {
    revalidatePath("/settings");
    revalidatePath("/");
  }
  return result;
}

export async function deleteAnnouncementAction(
  id: number,
): Promise<ApiResult<ActionResultDto>> {
  const result = await removeAnnouncement(id);
  if (result.ok) {
    revalidatePath("/settings");
    revalidatePath("/");
  }
  return result;
}
