import "server-only";
import { apiFetch, type ApiResult } from "@/shared/api/server-fetch";
import type { ActionResultDto, ScheduledTaskViewDto } from "@/shared/api/types";
import {
  SCHEDULE_ACTIONS,
  SCHEDULE_TYPES,
  type ScheduleAction,
  type ScheduledTask,
  type ScheduleType,
} from "@/modules/schedule/types";

function isAction(value: string): value is ScheduleAction {
  return (SCHEDULE_ACTIONS as readonly string[]).includes(value);
}

function isType(value: string): value is ScheduleType {
  return (SCHEDULE_TYPES as readonly string[]).includes(value);
}

function toTask(raw: ScheduledTaskViewDto): ScheduledTask {
  return {
    id: raw.id,
    serverId: raw.server_id,
    name: raw.name,
    action: isAction(raw.action) ? raw.action : "restart",
    enabled: raw.enabled,
    scheduleType: isType(raw.schedule_type) ? raw.schedule_type : "daily",
    scheduleValue: raw.schedule_value,
    nextRun: raw.next_run ?? null,
    lastStatus: raw.last_status ?? null,
  };
}

export async function listScheduledTasks(
  serverId: number,
): Promise<ApiResult<ScheduledTask[]>> {
  const result = await apiFetch<ScheduledTaskViewDto[]>(`/api/v1/servers/${serverId}/schedule`);
  if (!result.ok) return result;
  return { ok: true, data: result.data.map(toTask) };
}

export async function createScheduledTask(
  serverId: number,
  input: {
    readonly name: string;
    readonly action: ScheduleAction;
    readonly scheduleType: ScheduleType;
    readonly scheduleValue: string;
  },
): Promise<ApiResult<ScheduledTask>> {
  const result = await apiFetch<ScheduledTaskViewDto>(`/api/v1/servers/${serverId}/schedule`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      name: input.name,
      action: input.action,
      enabled: true,
      schedule_type: input.scheduleType,
      schedule_value: input.scheduleValue,
    }),
  });
  if (!result.ok) return result;
  return { ok: true, data: toTask(result.data) };
}

export async function toggleScheduledTask(
  serverId: number,
  taskId: number,
): Promise<ApiResult<ScheduledTask>> {
  const result = await apiFetch<ScheduledTaskViewDto>(
    `/api/v1/servers/${serverId}/schedule/${taskId}/toggle`,
    { method: "POST" },
  );
  if (!result.ok) return result;
  return { ok: true, data: toTask(result.data) };
}

export async function deleteScheduledTask(
  serverId: number,
  taskId: number,
): Promise<ApiResult<ActionResultDto>> {
  return apiFetch<ActionResultDto>(`/api/v1/servers/${serverId}/schedule/${taskId}`, {
    method: "DELETE",
  });
}
