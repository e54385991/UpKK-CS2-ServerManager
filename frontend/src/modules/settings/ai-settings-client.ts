import { toProfileAi, toProfileAiWire } from "@/modules/profile/ai-wire";
import type { ProfileAiPatch, ProfileAiSettings } from "@/modules/profile/types";
import { readAiApiError, toAiSettings, toAiSettingsWire } from "@/modules/settings/ai-wire";
import type { AiSystemPatch, AiSystemSettings } from "@/modules/settings/types";
import type {
  AssistantProviderTestViewDto,
  AssistantSystemSettingsViewDto,
  AssistantUserSettingsViewDto,
} from "@/shared/api/types";

export type AiClientResult<T> =
  | { readonly ok: true; readonly data: T }
  | { readonly ok: false; readonly status: number; readonly error: string };

const SETTINGS_PATH = "/ai-settings";

export async function loadSystemAiSettings(): Promise<AiClientResult<AiSystemSettings>> {
  const result = await requestJson<AssistantSystemSettingsViewDto>("system", "GET");
  if (!result.ok) return result;
  return { ok: true, data: toAiSettings(result.data) };
}

export async function saveSystemAiSettings(
  patch: AiSystemPatch,
): Promise<AiClientResult<AiSystemSettings>> {
  const result = await requestJson<AssistantSystemSettingsViewDto>(
    "system",
    "PUT",
    toAiSettingsWire(patch),
    20_000,
  );
  if (!result.ok) return result;
  return { ok: true, data: toAiSettings(result.data) };
}

export async function testSystemAiProvider(): Promise<
  AiClientResult<AssistantProviderTestViewDto>
> {
  return requestJson<AssistantProviderTestViewDto>("system", "POST", {}, 180_000);
}

export async function loadProfileAiSettings(): Promise<AiClientResult<ProfileAiSettings>> {
  const result = await requestJson<AssistantUserSettingsViewDto>("profile", "GET");
  if (!result.ok) return result;
  return { ok: true, data: toProfileAi(result.data) };
}

export async function saveProfileAiSettings(
  patch: ProfileAiPatch,
): Promise<AiClientResult<ProfileAiSettings>> {
  const result = await requestJson<AssistantUserSettingsViewDto>(
    "profile",
    "PUT",
    toProfileAiWire(patch),
    20_000,
  );
  if (!result.ok) return result;
  return { ok: true, data: toProfileAi(result.data) };
}

export async function testProfileAiProvider(): Promise<
  AiClientResult<AssistantProviderTestViewDto>
> {
  return requestJson<AssistantProviderTestViewDto>("profile", "POST", {}, 180_000);
}

async function requestJson<T>(
  scope: "profile" | "system",
  method: "GET" | "PUT" | "POST",
  body?: Record<string, unknown>,
  timeoutMs = 15_000,
): Promise<AiClientResult<T>> {
  try {
    const response = await fetch(`${SETTINGS_PATH}?scope=${scope}`, {
      method,
      credentials: "same-origin",
      cache: "no-store",
      headers: {
        accept: "application/json",
        ...(method === "GET" ? {} : { "content-type": "application/json" }),
      },
      body: method === "GET" ? undefined : JSON.stringify(body ?? {}),
      signal: AbortSignal.timeout(timeoutMs),
    });
    const parsed = await parseJsonBody(response);
    if (!response.ok) {
      return {
        ok: false,
        status: response.status,
        error: readAiApiError(parsed, response.status),
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
