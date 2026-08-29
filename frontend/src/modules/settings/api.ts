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
import { toAiSettings, toAiSettingsWire } from "@/modules/settings/ai-wire";
import {
  isEmailProvider,
  isProxyMode,
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
    body: JSON.stringify(toAiSettingsWire(patch)),
  });
  if (!result.ok) return result;
  return { ok: true, data: toAiSettings(result.data) };
}

export async function testAiSettings(): Promise<ApiResult<AssistantProviderTestViewDto>> {
  return apiFetch<AssistantProviderTestViewDto>("/api/v1/settings/ai/test", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: "{}",
    // Text + streamed tool probe can each use the provider timeout (default 60s).
    timeoutMs: 180_000,
  });
}
