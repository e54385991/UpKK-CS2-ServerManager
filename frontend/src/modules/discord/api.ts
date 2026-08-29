import "server-only";
import { apiFetch, type ApiResult } from "@/shared/api/server-fetch";
import type {
  ActionResultDto,
  AgentPolicyViewDto,
  DiscordBindingViewDto,
  DiscordBotTestViewDto,
  DiscordBotViewDto,
  DiscordGlobalBindingViewDto,
  DiscordMenuPushViewDto,
  DiscordOptionsViewDto,
} from "@/shared/api/types";
import type {
  AgentPolicy,
  DiscordBinding,
  DiscordBindingInput,
  DiscordBot,
  DiscordOptions,
} from "@/modules/discord/types";

function toBot(raw: DiscordBotViewDto): DiscordBot {
  return {
    enabled: raw.enabled,
    tokenConfigured: raw.token_configured,
    messageTriggerMode: raw.message_trigger_mode,
    username: raw.username ?? null,
    connectionStatus: raw.connection_status,
    lastError: raw.last_error ?? null,
    inviteUrl: raw.invite_url ?? null,
  };
}

export async function getDiscordBot(): Promise<ApiResult<DiscordBot>> {
  const result = await apiFetch<DiscordBotViewDto>("/api/v1/discord");
  if (!result.ok) return result;
  return { ok: true, data: toBot(result.data) };
}

export async function updateDiscordBot(input: {
  readonly token?: string;
  readonly enabled?: boolean;
  readonly messageTriggerMode?: "mention_only" | "mention_and_greetings";
}): Promise<ApiResult<DiscordBot>> {
  const result = await apiFetch<DiscordBotViewDto>("/api/v1/discord", {
    method: "PUT",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      token: input.token || null,
      enabled: input.enabled,
      message_trigger_mode: input.messageTriggerMode,
    }),
  });
  if (!result.ok) return result;
  return { ok: true, data: toBot(result.data) };
}

export async function deleteDiscordBot(): Promise<ApiResult<{ success: boolean; message: string }>> {
  return apiFetch<ActionResultDto>("/api/v1/discord", { method: "DELETE" });
}

export async function testDiscordBot(
  token?: string,
): Promise<ApiResult<{ success: boolean; username: string | null; message: string }>> {
  const result = await apiFetch<DiscordBotTestViewDto>("/api/v1/discord/test", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ token: token || null }),
  });
  if (!result.ok) return result;
  return {
    ok: true,
    data: {
      success: result.data.success,
      username: result.data.username ?? null,
      message: result.data.message,
    },
  };
}

function toOptions(raw: DiscordOptionsViewDto): DiscordOptions {
  return {
    tokenConfigured: raw.token_configured,
    guilds: (raw.guilds ?? []).map((item) => ({ id: item.id, name: item.name })),
    channels: (raw.channels ?? []).map((item) => ({ id: item.id, name: item.name })),
    roles: (raw.roles ?? []).map((item) => ({ id: item.id, name: item.name })),
    message: raw.message ?? null,
  };
}

function toBinding(raw: DiscordGlobalBindingViewDto | DiscordBindingViewDto): DiscordBinding {
  return {
    enabled: raw.enabled,
    effectiveEnabled: "effective_enabled" in raw ? raw.effective_enabled : undefined,
    disabledReason: "disabled_reason" in raw ? (raw.disabled_reason ?? null) : null,
    guildId: raw.guild_id ?? null,
    channelIds: raw.channel_ids ?? [],
    roleIds: raw.role_ids ?? [],
    userIds: raw.user_ids ?? [],
    allowChannelManagers: raw.allow_channel_managers,
    allowServerAdministrators: raw.allow_server_administrators,
    capabilities: raw.capabilities ?? [],
    configured: "configured" in raw ? raw.configured : undefined,
    serverCount: "server_count" in raw ? raw.server_count : undefined,
  };
}

function toPolicy(raw: AgentPolicyViewDto): AgentPolicy {
  return {
    serverId: raw.server_id,
    enabled: raw.enabled,
    effectiveEnabled: raw.effective_enabled,
    disabledReason: raw.disabled_reason ?? null,
    capabilities: raw.capabilities ?? [],
  };
}

function bindingBody(input: DiscordBindingInput) {
  return {
    enabled: input.enabled,
    guild_id: input.guildId,
    channel_ids: [...input.channelIds],
    role_ids: [...input.roleIds],
    user_ids: [...input.userIds],
    allow_channel_managers: input.allowChannelManagers,
    allow_server_administrators: input.allowServerAdministrators,
    capabilities: [...input.capabilities],
    sync_existing_servers: input.syncExistingServers ?? false,
  };
}

export async function getDiscordGlobalBinding(): Promise<ApiResult<DiscordBinding>> {
  const result = await apiFetch<DiscordGlobalBindingViewDto>("/api/v1/discord/global");
  if (!result.ok) return result;
  return { ok: true, data: toBinding(result.data) };
}

export async function updateDiscordGlobalBinding(
  input: DiscordBindingInput,
): Promise<ApiResult<DiscordBinding>> {
  const result = await apiFetch<DiscordGlobalBindingViewDto>("/api/v1/discord/global", {
    method: "PUT",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(bindingBody(input)),
  });
  if (!result.ok) return result;
  return { ok: true, data: toBinding(result.data) };
}

export async function getDiscordGlobalOptions(
  guildId?: string,
): Promise<ApiResult<DiscordOptions>> {
  const query = guildId ? `?guild_id=${encodeURIComponent(guildId)}` : "";
  const result = await apiFetch<DiscordOptionsViewDto>(`/api/v1/discord/global/options${query}`);
  if (!result.ok) return result;
  return { ok: true, data: toOptions(result.data) };
}

export async function getDiscordMenuOptions(
  guildId?: string,
): Promise<ApiResult<DiscordOptions>> {
  const query = guildId ? `?guild_id=${encodeURIComponent(guildId)}` : "";
  const result = await apiFetch<DiscordOptionsViewDto>(`/api/v1/discord/menu/options${query}`);
  if (!result.ok) return result;
  return { ok: true, data: toOptions(result.data) };
}

export async function pushDiscordMenu(
  guildId: string,
  channelId: string,
): Promise<ApiResult<{ messageId: string }>> {
  const result = await apiFetch<DiscordMenuPushViewDto>("/api/v1/discord/menu", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ guild_id: guildId, channel_id: channelId }),
  });
  if (!result.ok) return result;
  return { ok: true, data: { messageId: result.data.message_id } };
}

export async function getServerDiscordBinding(
  serverId: number,
): Promise<ApiResult<DiscordBinding>> {
  const result = await apiFetch<DiscordBindingViewDto>(`/api/v1/servers/${serverId}/discord`);
  if (!result.ok) return result;
  return { ok: true, data: toBinding(result.data) };
}

export async function updateServerDiscordBinding(
  serverId: number,
  input: DiscordBindingInput,
): Promise<ApiResult<DiscordBinding>> {
  const result = await apiFetch<DiscordBindingViewDto>(`/api/v1/servers/${serverId}/discord`, {
    method: "PUT",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(bindingBody(input)),
  });
  if (!result.ok) return result;
  return { ok: true, data: toBinding(result.data) };
}

export async function getServerDiscordOptions(
  serverId: number,
  guildId?: string,
): Promise<ApiResult<DiscordOptions>> {
  const query = guildId ? `?guild_id=${encodeURIComponent(guildId)}` : "";
  const result = await apiFetch<DiscordOptionsViewDto>(
    `/api/v1/servers/${serverId}/discord/options${query}`,
  );
  if (!result.ok) return result;
  return { ok: true, data: toOptions(result.data) };
}

export async function getServerAgentPolicy(
  serverId: number,
): Promise<ApiResult<AgentPolicy>> {
  const result = await apiFetch<AgentPolicyViewDto>(`/api/v1/servers/${serverId}/agent-policy`);
  if (!result.ok) return result;
  return { ok: true, data: toPolicy(result.data) };
}

export async function updateServerAgentPolicy(
  serverId: number,
  enabled: boolean,
  capabilities: readonly string[],
): Promise<ApiResult<AgentPolicy>> {
  const result = await apiFetch<AgentPolicyViewDto>(`/api/v1/servers/${serverId}/agent-policy`, {
    method: "PUT",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ enabled, capabilities: [...capabilities] }),
  });
  if (!result.ok) return result;
  return { ok: true, data: toPolicy(result.data) };
}
