"use server";

import { revalidatePath } from "next/cache";
import type { ApiResult } from "@/shared/api/server-fetch";
import type { ActionResultDto } from "@/shared/api/types";
import {
  createScheduledTask,
  deleteScheduledTask,
  listScheduledTasks,
  toggleScheduledTask,
} from "@/modules/schedule/api";
import type { ScheduleAction, ScheduledTask, ScheduleType } from "@/modules/schedule/types";

function revalidate(serverId: number) {
  revalidatePath(`/servers/${serverId}/schedule`);
}

export async function refreshScheduleAction(
  serverId: number,
): Promise<ApiResult<ScheduledTask[]>> {
  return listScheduledTasks(serverId);
}

export async function createScheduleAction(
  serverId: number,
  input: {
    readonly name: string;
    readonly action: ScheduleAction;
    readonly scheduleType: ScheduleType;
    readonly scheduleValue: string;
  },
): Promise<ApiResult<ScheduledTask>> {
  const result = await createScheduledTask(serverId, input);
  if (result.ok) revalidate(serverId);
  return result;
}

export async function toggleScheduleAction(
  serverId: number,
  taskId: number,
): Promise<ApiResult<ScheduledTask>> {
  const result = await toggleScheduledTask(serverId, taskId);
  if (result.ok) revalidate(serverId);
  return result;
}

export async function deleteScheduleAction(
  serverId: number,
  taskId: number,
): Promise<ApiResult<ActionResultDto>> {
  const result = await deleteScheduledTask(serverId, taskId);
  if (result.ok) revalidate(serverId);
  return result;
}
