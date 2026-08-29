import "server-only";
import { apiFetch, type ApiResult } from "@/shared/api/server-fetch";
import type {
  ActionResultDto,
  AssistantProviderTestViewDto,
  AssistantSystemSettingsViewDto,
  EmailTestResultDto,
  GmailAuthorizeResultDto,
  SystemSettingsViewDto,
} from "@/shared/api/types";
import {
  isEmailProvider,
  isProxyMode,
  type AiProtocol,
  type AiSystemPatch,
  type AiSystemSettings,
  type EmailProvider,
  type ProxyMode,
  type SettingsPatch,
  type SystemSettings,
} from "@/modules/settings/types";

function toSettings(raw: SystemSettingsViewDto): SystemSettings {
  return {
    defaultProxyMode: toProxyMode(raw.default_proxy_mode),
    githubProxyUrl: raw.github_proxy_url ?? null,
    hasGlobalGithubToken: raw.has_global_github_token,
    globalGithubTokenPrefix: raw.global_github_token_prefix ?? null,
    emailEnabled: raw.email_enabled,
    emailProvider: toEmailProvider(raw.email_provider),
    emailFromAddress: raw.email_from_address ?? null,
    emailFromName: raw.email_from_name ?? null,
    smtpHost: raw.smtp_host ?? null,
    smtpPort: raw.smtp_port ?? null,
    smtpUsername: raw.smtp_username ?? null,
    smtpUseTls: raw.smtp_use_tls,
    hasSmtpPassword: raw.has_smtp_password,
    hasGmailCredentials: raw.has_gmail_credentials,
    hasGmailToken: raw.has_gmail_token,
    gmailReady: raw.gmail_ready,
    updatedAt: raw.updated_at ?? null,
  };
}

function toProxyMode(value: string): ProxyMode {
  return isProxyMode(value) ? value : "panel";
}

function toEmailProvider(value: string): EmailProvider {
  return isEmailProvider(value) ? value : "smtp";
}

export function toWirePatch(patch: SettingsPatch): Record<string, unknown> {
  return {
    ...(patch.defaultProxyMode !== undefined
      ? { default_proxy_mode: patch.defaultProxyMode }
      : {}),
    ...(patch.githubProxyUrl !== undefined
      ? { github_proxy_url: patch.githubProxyUrl }
      : {}),
    ...(patch.globalGithubToken !== undefined
      ? { global_github_token: patch.globalGithubToken }
      : {}),
    ...(patch.clearGlobalGithubToken
      ? { clear_global_github_token: true }
      : {}),
    ...(patch.emailEnabled !== undefined
      ? { email_enabled: patch.emailEnabled }
      : {}),
    ...(patch.emailProvider !== undefined
      ? { email_provider: patch.emailProvider }
      : {}),
    ...(patch.emailFromAddress !== undefined
      ? { email_from_address: patch.emailFromAddress }
      : {}),
    ...(patch.emailFromName !== undefined
      ? { email_from_name: patch.emailFromName }
      : {}),
    ...(patch.smtpHost !== undefined ? { smtp_host: patch.smtpHost } : {}),
    ...(patch.smtpPort !== undefined ? { smtp_port: patch.smtpPort } : {}),
    ...(patch.smtpUsername !== undefined
      ? { smtp_username: patch.smtpUsername }
      : {}),
    ...(patch.smtpPassword !== undefined
      ? { smtp_password: patch.smtpPassword }
      : {}),
    ...(patch.smtpUseTls !== undefined
      ? { smtp_use_tls: patch.smtpUseTls }
      : {}),
  };
}

export async function getSettings(): Promise<ApiResult<SystemSettings>> {
  const result = await apiFetch<SystemSettingsViewDto>("/api/v1/settings");
  if (!result.ok) return result;
  return { ok: true, data: toSettings(result.data) };
}

export async function putSettings(
  patch: SettingsPatch,
): Promise<ApiResult<SystemSettings>> {
  const result = await apiFetch<SystemSettingsViewDto>("/api/v1/settings", {
    method: "PUT",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(toWirePatch(patch)),
  });
  if (!result.ok) return result;
  return { ok: true, data: toSettings(result.data) };
}

export async function postTestEmail(
  testEmail: string,
): Promise<ApiResult<EmailTestResultDto>> {
  return apiFetch<EmailTestResultDto>("/api/v1/settings/test-email", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ test_email: testEmail }),
  });
}

export async function putGmailCredentials(
  credentialsJson: string,
): Promise<ApiResult<ActionResultDto>> {
  return apiFetch<ActionResultDto>("/api/v1/settings/gmail/credentials", {
    method: "PUT",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ credentials_json: credentialsJson }),
  });
}

export async function getGmailAuthorize(): Promise<
  ApiResult<GmailAuthorizeResultDto>
> {
  return apiFetch<GmailAuthorizeResultDto>("/api/v1/settings/gmail/authorize");
}

export async function deleteGmailAuthorization(): Promise<
  ApiResult<ActionResultDto>
> {
  return apiFetch<ActionResultDto>("/api/v1/settings/gmail", {
    method: "DELETE",
  });
}

function toAiProtocol(value: string): AiProtocol {
  return value === "responses" ? "responses" : "chat_completions";
}

function toAiSettings(raw: AssistantSystemSettingsViewDto): AiSystemSettings {
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

export async function getAiSettings(): Promise<ApiResult<AiSystemSettings>> {
  const result = await apiFetch<AssistantSystemSettingsViewDto>("/api/v1/settings/ai");
  if (!result.ok) return result;
  return { ok: true, data: toAiSettings(result.data) };
}

export async function putAiSettings(
  patch: AiSystemPatch,
): Promise<ApiResult<AiSystemSettings>> {
  const result = await apiFetch<AssistantSystemSettingsViewDto>("/api/v1/settings/ai", {
    method: "PUT",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
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
    }),
  });
  if (!result.ok) return result;
  return { ok: true, data: toAiSettings(result.data) };
}

export async function testAiSettings(input: {
  readonly baseUrl?: string;
  readonly model?: string;
  readonly apiKey?: string;
}): Promise<ApiResult<AssistantProviderTestViewDto>> {
  return apiFetch<AssistantProviderTestViewDto>("/api/v1/settings/ai/test", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      base_url: input.baseUrl || null,
      model: input.model || null,
      api_key: input.apiKey || null,
    }),
  });
}
