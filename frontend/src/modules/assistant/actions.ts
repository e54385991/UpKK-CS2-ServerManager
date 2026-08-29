"use server";

import { revalidatePath } from "next/cache";
import type { ApiResult } from "@/shared/api/server-fetch";
import {
  createAssistantConversation,
  decideAssistantTool,
  getAssistantConversation,
  getAssistantWorkspace,
  interruptAssistantConversation,
  sendAssistantMessage,
} from "@/modules/assistant/api";
import type {
  AssistantConversation,
  AssistantConversationDetail,
  AssistantRun,
  AssistantWorkspace,
} from "@/modules/assistant/types";

function revalidateAssistant() {
  revalidatePath("/assistant");
}

export async function refreshAssistantAction(): Promise<ApiResult<AssistantWorkspace>> {
  return getAssistantWorkspace();
}

export async function createConversationAction(
  title?: string,
): Promise<ApiResult<AssistantConversation>> {
  const result = await createAssistantConversation(title);
  if (result.ok) revalidateAssistant();
  return result;
}

export async function loadConversationAction(
  conversationId: string,
): Promise<ApiResult<AssistantConversationDetail>> {
  return getAssistantConversation(conversationId);
}

export async function sendMessageAction(
  conversationId: string,
  content: string,
): Promise<ApiResult<AssistantRun>> {
  const result = await sendAssistantMessage(conversationId, content);
  if (result.ok) revalidateAssistant();
  return result;
}

export async function interruptConversationAction(
  conversationId: string,
): Promise<ApiResult<{ success: boolean; message: string }>> {
  const result = await interruptAssistantConversation(conversationId);
  if (result.ok) revalidateAssistant();
  return result;
}

export async function decideToolAction(
  runId: string,
  toolRunId: string,
  decision: "approve" | "reject",
  argumentsHash: string,
): Promise<ApiResult<{ success: boolean; message: string }>> {
  const result = await decideAssistantTool(runId, toolRunId, decision, argumentsHash);
  if (result.ok) revalidateAssistant();
  return result;
}
