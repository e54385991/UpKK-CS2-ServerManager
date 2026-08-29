import "server-only";
import { apiFetch, type ApiResult } from "@/shared/api/server-fetch";
import type {
  ActionResultDto,
  AssistantProviderTestViewDto,
  AssistantUserSettingsViewDto,
  ProfileApiKeyViewDto,
  ProfileGsltViewDto,
  ProfileS3TestViewDto,
  ProfileS3ViewDto,
  ProfileViewDto,
} from "@/shared/api/types";
import { toProfileAi, toProfileAiWire } from "@/modules/profile/ai-wire";
import type {
  ProfileAiPatch,
  ProfileAiSettings,
  ProfileApiKey,
  ProfileCredentialsPatch,
  ProfileGslt,
  ProfileS3Patch,
  ProfileS3Settings,
  ProfileS3Test,
  ProfileSettings,
} from "@/modules/profile/types";

function toProfile(raw: ProfileViewDto): ProfileSettings {
  return {
    id: raw.id,
    username: raw.username,
    email: raw.email ?? null,
    isAdmin: raw.is_admin,
    isActive: raw.is_active,
    createdAt: raw.created_at ?? null,
    steamcmdMaxRetries: raw.steamcmd_max_retries,
    steamcmdMaxRetriesDefault: raw.steamcmd_max_retries_default,
    steamcmdMaxRetriesLimit: raw.steamcmd_max_retries_limit,
    hasSteamApiKey: raw.has_steam_api_key,
    steamApiKeyPrefix: raw.steam_api_key_prefix ?? null,
    hasGithubToken: raw.has_github_token,
    githubTokenPrefix: raw.github_token_prefix ?? null,
    hasApiKey: raw.has_api_key,
  };
}

function toS3(raw: ProfileS3ViewDto): ProfileS3Settings {
  return {
    enabled: raw.enabled,
    endpointUrl: raw.endpoint_url ?? null,
    region: raw.region ?? null,
    bucket: raw.bucket ?? null,
    accessKeyId: raw.access_key_id ?? null,
    prefix: raw.prefix ?? null,
    useSsl: raw.use_ssl,
    retentionCount: raw.retention_count,
    hasSecret: raw.has_secret,
    isConfigured: raw.is_configured,
  };
}

export async function getProfile(): Promise<ApiResult<ProfileSettings>> {
  const result = await apiFetch<ProfileViewDto>("/api/v1/profile");
  if (!result.ok) return result;
  return { ok: true, data: toProfile(result.data) };
}

export async function patchProfile(
  steamcmdMaxRetries: number,
): Promise<ApiResult<ProfileSettings>> {
  const result = await apiFetch<ProfileViewDto>("/api/v1/profile", {
    method: "PATCH",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ steamcmd_max_retries: steamcmdMaxRetries }),
  });
  if (!result.ok) return result;
  return { ok: true, data: toProfile(result.data) };
}

export async function patchProfileCredentials(
  patch: ProfileCredentialsPatch,
): Promise<ApiResult<ProfileSettings>> {
  const result = await apiFetch<ProfileViewDto>("/api/v1/profile", {
    method: "PATCH",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      ...(patch.email !== undefined ? { email: patch.email } : {}),
      ...(patch.clearSteamApiKey
        ? { clear_steam_api_key: true }
        : patch.steamApiKey !== undefined
          ? { steam_api_key: patch.steamApiKey }
          : {}),
      ...(patch.clearGithubToken
        ? { clear_github_token: true }
        : patch.githubToken !== undefined
          ? { github_token: patch.githubToken }
          : {}),
      captcha_token: patch.captchaToken,
      captcha_code: patch.captchaCode,
    }),
  });
  if (!result.ok) return result;
  return { ok: true, data: toProfile(result.data) };
}

export async function changePassword(input: {
  readonly currentPassword: string;
  readonly newPassword: string;
  readonly confirmPassword: string;
  readonly captchaToken: string;
  readonly captchaCode: string;
}): Promise<ApiResult<ActionResultDto>> {
  return apiFetch<ActionResultDto>("/api/v1/profile/password", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      current_password: input.currentPassword,
      new_password: input.newPassword,
      confirm_password: input.confirmPassword,
      captcha_token: input.captchaToken,
      captcha_code: input.captchaCode,
    }),
  });
}

export async function getApiKey(): Promise<ApiResult<ProfileApiKey>> {
  const result = await apiFetch<ProfileApiKeyViewDto>("/api/v1/profile/api-key");
  if (!result.ok) return result;
  return {
    ok: true,
    data: { apiKey: result.data.api_key, createdAt: result.data.created_at ?? null },
  };
}

export async function generateApiKey(input: {
  readonly captchaToken: string;
  readonly captchaCode: string;
}): Promise<ApiResult<ProfileApiKey>> {
  const result = await apiFetch<ProfileApiKeyViewDto>("/api/v1/profile/api-key", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      captcha_token: input.captchaToken,
      captcha_code: input.captchaCode,
    }),
  });
  if (!result.ok) return result;
  return {
    ok: true,
    data: { apiKey: result.data.api_key, createdAt: result.data.created_at ?? null },
  };
}

export async function revokeApiKey(): Promise<ApiResult<ActionResultDto>> {
  return apiFetch<ActionResultDto>("/api/v1/profile/api-key", { method: "DELETE" });
}

export async function generateGslt(input: {
  readonly serverName?: string;
  readonly captchaToken: string;
  readonly captchaCode: string;
}): Promise<ApiResult<ProfileGslt>> {
  const result = await apiFetch<ProfileGsltViewDto>(
    "/api/v1/profile/gslt",
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        ...(input.serverName ? { server_name: input.serverName } : {}),
        captcha_token: input.captchaToken,
        captcha_code: input.captchaCode,
      }),
      timeoutMs: 25_000,
    },
  );
  if (!result.ok) return result;
  return {
    ok: true,
    data: {
      loginToken: result.data.login_token,
      steamid: result.data.steamid ?? null,
    },
  };
}

export async function getS3Settings(): Promise<ApiResult<ProfileS3Settings>> {
  const result = await apiFetch<ProfileS3ViewDto>("/api/v1/profile/s3");
  if (!result.ok) return result;
  return { ok: true, data: toS3(result.data) };
}

export async function putS3Settings(
  patch: ProfileS3Patch,
): Promise<ApiResult<ProfileS3Settings>> {
  const result = await apiFetch<ProfileS3ViewDto>("/api/v1/profile/s3", {
    method: "PUT",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      enabled: patch.enabled,
      endpoint_url: patch.endpointUrl,
      region: patch.region,
      bucket: patch.bucket,
      access_key_id: patch.accessKeyId,
      ...(patch.secretAccessKey ? { secret_access_key: patch.secretAccessKey } : {}),
      prefix: patch.prefix,
      use_ssl: patch.useSsl,
      retention_count: patch.retentionCount,
      clear_secret: Boolean(patch.clearSecret),
      captcha_token: patch.captchaToken,
      captcha_code: patch.captchaCode,
    }),
  });
  if (!result.ok) return result;
  return { ok: true, data: toS3(result.data) };
}

export async function testS3Settings(): Promise<ApiResult<ProfileS3Test>> {
  const result = await apiFetch<ProfileS3TestViewDto>("/api/v1/profile/s3/test", {
    method: "POST",
  });
  if (!result.ok) return result;
  return {
    ok: true,
    data: {
      success: result.data.success,
      message: result.data.message,
      steps: result.data.steps ?? [],
    },
  };
}

export async function getProfileAi(): Promise<ApiResult<ProfileAiSettings>> {
  const result = await apiFetch<AssistantUserSettingsViewDto>("/api/v1/profile/ai");
  if (!result.ok) return result;
  return { ok: true, data: toProfileAi(result.data) };
}

export async function putProfileAi(
  patch: ProfileAiPatch,
): Promise<ApiResult<ProfileAiSettings>> {
  const result = await apiFetch<AssistantUserSettingsViewDto>("/api/v1/profile/ai", {
    method: "PUT",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(toProfileAiWire(patch)),
  });
  if (!result.ok) return result;
  return { ok: true, data: toProfileAi(result.data) };
}

export async function testProfileAi(): Promise<ApiResult<AssistantProviderTestViewDto>> {
  return apiFetch<AssistantProviderTestViewDto>("/api/v1/profile/ai/test", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: "{}",
    timeoutMs: 180_000,
  });
}
