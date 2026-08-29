import type {
  AssistantConversationDetailViewDto,
  AssistantConversationViewDto,
  AssistantRunDetailViewDto,
  AssistantRunViewDto,
  AssistantToolViewDto,
  AssistantWorkspaceViewDto,
} from "@/shared/api/types";
import type {
  AssistantConversation,
  AssistantConversationDetail,
  AssistantMessage,
  AssistantRun,
  AssistantRunDetail,
  AssistantTool,
  AssistantWorkspace,
} from "@/modules/assistant/types";

export function toConversation(raw: AssistantConversationViewDto): AssistantConversation {
  return {
    id: raw.id,
    serverId: raw.server_id ?? null,
    title: raw.title,
  };
}

export function toMessage(
  raw: NonNullable<AssistantConversationDetailViewDto["messages"]>[number],
): AssistantMessage {
  return {
    id: raw.id,
    role: raw.role,
    content: raw.content ?? null,
    toolName: raw.tool_name ?? null,
  };
}

export function toWorkspace(raw: AssistantWorkspaceViewDto): AssistantWorkspace {
  return {
    providerReady: raw.provider_ready,
    mode: raw.mode,
    model: raw.model ?? null,
    conversations: (raw.conversations ?? []).map(toConversation),
  };
}

export function toConversationDetail(
  raw: AssistantConversationDetailViewDto,
): AssistantConversationDetail {
  return {
    ...toConversation(raw),
    messages: (raw.messages ?? []).map(toMessage),
  };
}

export function toRun(raw: AssistantRunViewDto): AssistantRun {
  return {
    id: raw.id,
    conversationId: raw.conversation_id,
    status: raw.status,
    error: raw.error ?? null,
  };
}

export function toTool(raw: AssistantToolViewDto): AssistantTool {
  return {
    id: raw.id,
    toolName: raw.tool_name,
    argumentsHash: raw.arguments_hash,
    risk: raw.risk,
    status: raw.status,
    requiresApproval: raw.requires_approval,
    error: raw.error ?? null,
    arguments: {},
    summary: null,
  };
}

export function toRunDetail(raw: AssistantRunDetailViewDto): AssistantRunDetail {
  return {
    ...toRun(raw),
    tools: (raw.tools ?? []).map(toTool),
  };
}

export function readAssistantError(body: unknown, status: number): string {
  const fallback = `Request failed with ${status}`;
  if (!body || typeof body !== "object") return fallback;
  const record = body as { detail?: unknown; message?: unknown };
  if (typeof record.detail === "string" && record.detail.trim()) {
    return record.detail;
  }
  if (Array.isArray(record.detail) && record.detail.length > 0) {
    const first = record.detail[0] as { msg?: unknown };
    if (typeof first?.msg === "string" && first.msg.trim()) {
      return first.msg;
    }
  }
  if (record.detail && typeof record.detail === "object") {
    const detail = record.detail as { message?: unknown };
    if (typeof detail.message === "string" && detail.message.trim()) {
      return detail.message;
    }
  }
  if (typeof record.message === "string" && record.message.trim()) {
    return record.message;
  }
  return fallback;
}

export type AssistantSseEvent = {
  readonly type: string;
  readonly payload: Record<string, unknown>;
};

export function parseAssistantSseData(data: string): AssistantSseEvent | null {
  try {
    const parsed = JSON.parse(data) as {
      type?: unknown;
      payload?: unknown;
    };
    if (typeof parsed.type !== "string" || !parsed.type) return null;
    return {
      type: parsed.type,
      payload:
        parsed.payload && typeof parsed.payload === "object" && !Array.isArray(parsed.payload)
          ? (parsed.payload as Record<string, unknown>)
          : {},
    };
  } catch {
    return null;
  }
}

export function toolFromApprovalPayload(
  payload: Record<string, unknown>,
): AssistantTool | null {
  const id = stringField(payload.tool_run_id) || stringField(payload.id);
  const toolName = stringField(payload.tool_name);
  const argumentsHash = stringField(payload.arguments_hash);
  if (!id || !toolName || !argumentsHash) return null;
  return {
    id,
    toolName,
    argumentsHash,
    risk: stringField(payload.risk) || "write",
    status: stringField(payload.status) || "pending_approval",
    requiresApproval: true,
    error: stringField(payload.error),
    arguments: recordField(payload.arguments),
    summary: payload.summary ?? payload.plan_snapshot ?? null,
  };
}

function stringField(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function recordField(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) return {};
  return value as Record<string, unknown>;
}
