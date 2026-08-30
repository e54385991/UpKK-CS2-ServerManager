import type { AssistantUserSettingsViewDto } from "@/shared/api/types";
import type { ProfileAiPatch, ProfileAiSettings } from "@/modules/profile/types";

export const EMPTY_PROFILE_AI_SETTINGS: ProfileAiSettings = {
  mode: "global",
  baseUrl: null,
  model: null,
  apiProtocol: "chat_completions",
  apiKeyConfigured: false,
  reasoningEffort: null,
  temperature: null,
  topP: null,
  maxCompletionTokens: 2048,
  tokenLimitParameter: "max_completion_tokens",
  frequencyPenalty: null,
  presencePenalty: null,
  verbosity: null,
  parallelToolCalls: null,
  providerTested: false,
  toolCallingTested: false,
  streamingTested: false,
  effectiveEnabled: false,
  effectiveSource: "none",
};

export function toProfileAi(raw: AssistantUserSettingsViewDto): ProfileAiSettings {
  return {
    mode: raw.mode === "custom" ? "custom" : "global",
    baseUrl: raw.base_url ?? null,
    model: raw.model ?? null,
    apiProtocol: raw.api_protocol === "responses" ? "responses" : "chat_completions",
    apiKeyConfigured: raw.api_key_configured,
    reasoningEffort: raw.reasoning_effort ?? null,
    temperature: raw.temperature ?? null,
    topP: raw.top_p ?? null,
    maxCompletionTokens: raw.max_completion_tokens,
    tokenLimitParameter: raw.token_limit_parameter,
    frequencyPenalty: raw.frequency_penalty ?? null,
    presencePenalty: raw.presence_penalty ?? null,
    verbosity: raw.verbosity ?? null,
    parallelToolCalls: raw.parallel_tool_calls ?? null,
    providerTested: raw.provider_tested,
    toolCallingTested: raw.tool_calling_tested,
    streamingTested: raw.streaming_tested,
    effectiveEnabled: raw.effective_enabled,
    effectiveSource: raw.effective_source,
  };
}

export function toProfileAiWire(patch: ProfileAiPatch): Record<string, unknown> {
  return {
    mode: patch.mode,
    ...(patch.baseUrl !== undefined ? { base_url: patch.baseUrl } : {}),
    ...(patch.model !== undefined ? { model: patch.model } : {}),
    ...(patch.apiProtocol !== undefined ? { api_protocol: patch.apiProtocol } : {}),
    ...(patch.apiKey !== undefined ? { api_key: patch.apiKey } : {}),
    ...(patch.clearApiKey ? { clear_api_key: true } : {}),
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
  };
}
