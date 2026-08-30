import {
  readAssistantError,
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
import type {
  ActionResultDto,
  AssistantConversationDetailViewDto,
  AssistantConversationViewDto,
  AssistantRunDetailViewDto,
  AssistantRunViewDto,
  AssistantWorkspaceViewDto,
} from "@/shared/api/types";

export type AssistantClientResult<T> =
  | { readonly ok: true; readonly data: T }
  | { readonly ok: false; readonly status: number; readonly error: string };

const PATH = "/ai-assistant";

export async function loadAssistantWorkspace(): Promise<
  AssistantClientResult<AssistantWorkspace>
> {
  const result = await requestJson<AssistantWorkspaceViewDto>("workspace", "GET");
  if (!result.ok) return result;
  return { ok: true, data: toWorkspace(result.data) };
}

export async function createAssistantConversationClient(
  serverId: number | null,
  title?: string,
): Promise<AssistantClientResult<AssistantConversation>> {
  const result = await requestJson<AssistantConversationViewDto>("create", "POST", {
    title: title ?? null,
    server_id: serverId,
  });
  if (!result.ok) return result;
  return { ok: true, data: toConversation(result.data) };
}

export async function loadAssistantConversationClient(
  conversationId: string,
): Promise<AssistantClientResult<AssistantConversationDetail>> {
  const result = await requestJson<AssistantConversationDetailViewDto>(
    "conversation",
    "GET",
    undefined,
    { id: conversationId },
  );
  if (!result.ok) return result;
  return { ok: true, data: toConversationDetail(result.data) };
}

export async function sendAssistantMessageClient(
  conversationId: string,
  content: string,
): Promise<AssistantClientResult<AssistantRun>> {
  const result = await requestJson<AssistantRunViewDto>(
    "send",
    "POST",
    { content },
    { id: conversationId },
  );
  if (!result.ok) return result;
  return { ok: true, data: toRun(result.data) };
}

export async function interruptAssistantConversationClient(
  conversationId: string,
): Promise<AssistantClientResult<{ success: boolean; message: string }>> {
  return requestJson<ActionResultDto>("interrupt", "POST", {}, { id: conversationId });
}

export async function loadAssistantRunClient(
  runId: string,
): Promise<AssistantClientResult<AssistantRunDetail>> {
  const result = await requestJson<AssistantRunDetailViewDto>("run", "GET", undefined, {
    id: runId,
  });
  if (!result.ok) return result;
  return { ok: true, data: toRunDetail(result.data) };
}

export async function decideAssistantToolClient(
  runId: string,
  toolRunId: string,
  decision: "approve" | "reject",
  argumentsHash: string,
): Promise<AssistantClientResult<{ success: boolean; message: string }>> {
  return requestJson<ActionResultDto>(
    "decide",
    "POST",
    { decision, arguments_hash: argumentsHash },
    { runId, toolId: toolRunId },
  );
}

async function requestJson<T>(
  action: string,
  method: "GET" | "POST",
  body?: Record<string, unknown>,
  extra?: Record<string, string>,
): Promise<AssistantClientResult<T>> {
  const params = new URLSearchParams({ action, ...extra });
  try {
    const response = await fetch(`${PATH}?${params}`, {
      method,
      credentials: "same-origin",
      cache: "no-store",
      headers: {
        accept: "application/json",
        ...(method === "GET" ? {} : { "content-type": "application/json" }),
      },
      body: method === "GET" ? undefined : JSON.stringify(body ?? {}),
      signal: AbortSignal.timeout(20_000),
    });
    const parsed = await parseJsonBody(response);
    if (!response.ok) {
      return {
        ok: false,
        status: response.status,
        error: readAssistantError(parsed, response.status),
      };
    }
    return { ok: true, data: parsed as T };
  } catch (error) {
    return {
      ok: false,
      status: 0,
      error: error instanceof Error ? error.message : "network error",
    };
  }
}

async function parseJsonBody(response: Response): Promise<unknown> {
  const text = await response.text();
  if (!text.trim()) return {};
  try {
    return JSON.parse(text) as unknown;
  } catch {
    return { detail: text.slice(0, 280) };
  }
}
