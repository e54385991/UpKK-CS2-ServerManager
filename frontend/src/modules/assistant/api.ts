import "server-only";
import { apiFetch, type ApiResult } from "@/shared/api/server-fetch";
import type {
  ActionResultDto,
  AssistantConversationDetailViewDto,
  AssistantConversationViewDto,
  AssistantRunDetailViewDto,
  AssistantRunViewDto,
  AssistantWorkspaceViewDto,
} from "@/shared/api/types";
import {
  toConversation,
  toConversationDetail,
  toRun,
  toRunDetail,
  toWorkspace,
} from "@/modules/assistant/assistant-wire";
import type {
  AssistantConversation,
  AssistantConversationDetail,
  AssistantRun,
  AssistantRunDetail,
  AssistantWorkspace,
} from "@/modules/assistant/types";

export async function getAssistantWorkspace(): Promise<ApiResult<AssistantWorkspace>> {
  const result = await apiFetch<AssistantWorkspaceViewDto>("/api/v1/assistant");
  if (!result.ok) return result;
  return { ok: true, data: toWorkspace(result.data) };
}

export async function createAssistantConversation(
  title?: string,
  serverId?: number | null,
): Promise<ApiResult<AssistantConversation>> {
  const result = await apiFetch<AssistantConversationViewDto>("/api/v1/assistant/conversations", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ title: title ?? null, server_id: serverId ?? null }),
  });
  if (!result.ok) return result;
  return { ok: true, data: toConversation(result.data) };
}

export async function getAssistantConversation(
  conversationId: string,
): Promise<ApiResult<AssistantConversationDetail>> {
  const result = await apiFetch<AssistantConversationDetailViewDto>(
    `/api/v1/assistant/conversations/${conversationId}`,
  );
  if (!result.ok) return result;
  return { ok: true, data: toConversationDetail(result.data) };
}

export async function sendAssistantMessage(
  conversationId: string,
  content: string,
): Promise<ApiResult<AssistantRun>> {
  const result = await apiFetch<AssistantRunViewDto>(
    `/api/v1/assistant/conversations/${conversationId}/messages`,
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ content }),
    },
  );
  if (!result.ok) return result;
  return { ok: true, data: toRun(result.data) };
}

export async function interruptAssistantConversation(
  conversationId: string,
): Promise<ApiResult<{ success: boolean; message: string }>> {
  return apiFetch<ActionResultDto>(
    `/api/v1/assistant/conversations/${conversationId}/interrupt`,
    { method: "POST" },
  );
}

export async function getAssistantRun(
  runId: string,
): Promise<ApiResult<AssistantRunDetail>> {
  const result = await apiFetch<AssistantRunDetailViewDto>(`/api/v1/assistant/runs/${runId}`);
  if (!result.ok) return result;
  return { ok: true, data: toRunDetail(result.data) };
}

export async function decideAssistantTool(
  runId: string,
  toolRunId: string,
  decision: "approve" | "reject",
  argumentsHash: string,
): Promise<ApiResult<{ success: boolean; message: string }>> {
  return apiFetch<ActionResultDto>(`/api/v1/assistant/runs/${runId}/tools/${toolRunId}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ decision, arguments_hash: argumentsHash }),
  });
}
