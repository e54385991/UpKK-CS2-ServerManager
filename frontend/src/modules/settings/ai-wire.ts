import type { AssistantSystemSettingsViewDto } from "@/shared/api/types";
import type { AiProtocol, AiSystemPatch, AiSystemSettings } from "@/modules/settings/types";

export const EMPTY_AI_SYSTEM_SETTINGS: AiSystemSettings = {
  enabled: false,
  baseUrl: null,
  model: null,
  apiProtocol: "chat_completions",
  apiKeyConfigured: false,
  adminPrompt: null,
  privateEndpointAllowlist: [],
  reasoningEffort: null,
  temperature: null,
  topP: null,
  maxCompletionTokens: 2048,
  tokenLimitParameter: "max_completion_tokens",
  frequencyPenalty: null,
  presencePenalty: null,
  verbosity: null,
  parallelToolCalls: null,
  requestTimeoutSeconds: 60,
  historyRetentionDays: 7,
  maxProviderRounds: 200,
  maxToolCallsPerRound: 200,
  providerTested: false,
  toolCallingTested: false,
  streamingTested: false,
};

export function toAiProtocol(value: string): AiProtocol {
  return value === "responses" ? "responses" : "chat_completions";
}

export function toAiSettings(raw: AssistantSystemSettingsViewDto): AiSystemSettings {
  return {
    enabled: raw.enabled,
    baseUrl: raw.base_url ?? null,
    model: raw.model ?? null,
    apiProtocol: toAiProtocol(raw.api_protocol),
    apiKeyConfigured: raw.api_key_configured,
    adminPrompt: raw.admin_prompt ?? null,
    privateEndpointAllowlist: raw.private_endpoint_allowlist ?? [],
    reasoningEffort: raw.reasoning_effort ?? null,
    temperature: raw.temperature ?? null,
    topP: raw.top_p ?? null,
    maxCompletionTokens: raw.max_completion_tokens,
    tokenLimitParameter: raw.token_limit_parameter,
    frequencyPenalty: raw.frequency_penalty ?? null,
    presencePenalty: raw.presence_penalty ?? null,
    verbosity: raw.verbosity ?? null,
    parallelToolCalls: raw.parallel_tool_calls ?? null,
    requestTimeoutSeconds: raw.request_timeout_seconds,
    historyRetentionDays: raw.history_retention_days,
    maxProviderRounds: raw.max_provider_rounds,
    maxToolCallsPerRound: raw.max_tool_calls_per_round,
    providerTested: raw.provider_tested,
    toolCallingTested: raw.tool_calling_tested,
    streamingTested: raw.streaming_tested,
  };
}

export function toAiSettingsWire(patch: AiSystemPatch): Record<string, unknown> {
  return {
    ...(patch.enabled !== undefined ? { enabled: patch.enabled } : {}),
    ...(patch.baseUrl !== undefined ? { base_url: patch.baseUrl } : {}),
    ...(patch.model !== undefined ? { model: patch.model } : {}),
    ...(patch.apiProtocol !== undefined ? { api_protocol: patch.apiProtocol } : {}),
    ...(patch.apiKey !== undefined ? { api_key: patch.apiKey } : {}),
    ...(patch.clearApiKey ? { clear_api_key: true } : {}),
    ...(patch.adminPrompt !== undefined ? { admin_prompt: patch.adminPrompt } : {}),
    ...(patch.privateEndpointAllowlist !== undefined
      ? { private_endpoint_allowlist: patch.privateEndpointAllowlist }
      : {}),
    ...(patch.reasoningEffort !== undefined
      ? { reasoning_effort: patch.reasoningEffort }
      : {}),
    ...(patch.temperature !== undefined ? { temperature: patch.temperature } : {}),
    ...(patch.topP !== undefined ? { top_p: patch.topP } : {}),
    ...(patch.maxCompletionTokens !== undefined
      ? { max_completion_tokens: patch.maxCompletionTokens }
      : {}),
    ...(patch.tokenLimitParameter !== undefined
      ? { token_limit_parameter: patch.tokenLimitParameter }
      : {}),
    ...(patch.frequencyPenalty !== undefined
      ? { frequency_penalty: patch.frequencyPenalty }
      : {}),
    ...(patch.presencePenalty !== undefined
      ? { presence_penalty: patch.presencePenalty }
      : {}),
    ...(patch.verbosity !== undefined ? { verbosity: patch.verbosity } : {}),
    ...(patch.parallelToolCalls !== undefined
      ? { parallel_tool_calls: patch.parallelToolCalls }
      : {}),
    ...(patch.requestTimeoutSeconds !== undefined
      ? { request_timeout_seconds: patch.requestTimeoutSeconds }
      : {}),
    ...(patch.historyRetentionDays !== undefined
      ? { history_retention_days: patch.historyRetentionDays }
      : {}),
    ...(patch.maxProviderRounds !== undefined
      ? { max_provider_rounds: patch.maxProviderRounds }
      : {}),
    ...(patch.maxToolCallsPerRound !== undefined
      ? { max_tool_calls_per_round: patch.maxToolCallsPerRound }
      : {}),
  };
}

export function readAiApiError(body: unknown, status: number): string {
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
