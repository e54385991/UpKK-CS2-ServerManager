import "server-only";
import { apiFetch, type ApiResult } from "@/shared/api/server-fetch";
import type {
  ActionResultDto,
  AssistantConversationDetailViewDto,
  AssistantConversationViewDto,
  AssistantRunViewDto,
  AssistantWorkspaceViewDto,
} from "@/shared/api/types";
import type {
  AssistantConversation,
  AssistantConversationDetail,
  AssistantMessage,
  AssistantRun,
  AssistantWorkspace,
} from "@/modules/assistant/types";

function toConversation(raw: AssistantConversationViewDto): AssistantConversation {
  return {
    id: raw.id,
    serverId: raw.server_id ?? null,
    title: raw.title,
  };
}

function toMessage(
  raw: NonNullable<AssistantConversationDetailViewDto["messages"]>[number],
): AssistantMessage {
  return {
    id: raw.id,
    role: raw.role,
    content: raw.content ?? null,
    toolName: raw.tool_name ?? null,
  };
}

function toWorkspace(raw: AssistantWorkspaceViewDto): AssistantWorkspace {
  return {
    providerReady: raw.provider_ready,
    mode: raw.mode,
    model: raw.model ?? null,
    conversations: (raw.conversations ?? []).map(toConversation),
  };
}

function toRun(raw: AssistantRunViewDto): AssistantRun {
  return {
    id: raw.id,
    conversationId: raw.conversation_id,
    status: raw.status,
    error: raw.error ?? null,
  };
}

export async function getAssistantWorkspace(): Promise<ApiResult<AssistantWorkspace>> {
  const result = await apiFetch<AssistantWorkspaceViewDto>("/api/v1/assistant");
  if (!result.ok) return result;
  return { ok: true, data: toWorkspace(result.data) };
}

export async function createAssistantConversation(
  title?: string,
): Promise<ApiResult<AssistantConversation>> {
  const result = await apiFetch<AssistantConversationViewDto>("/api/v1/assistant/conversations", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ title: title ?? null }),
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
  return {
    ok: true,
    data: {
      ...toConversation(result.data),
      messages: (result.data.messages ?? []).map(toMessage),
    },
  };
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
