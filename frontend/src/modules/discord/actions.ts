"use server";

import { revalidatePath } from "next/cache";
import type { ApiResult } from "@/shared/api/server-fetch";
import {
  deleteDiscordBot,
  getDiscordBot,
  getDiscordGlobalBinding,
  getDiscordGlobalOptions,
  getDiscordMenuOptions,
  getServerAgentPolicy,
  getServerDiscordBinding,
  getServerDiscordOptions,
  pushDiscordMenu,
  testDiscordBot,
  updateDiscordBot,
  updateDiscordGlobalBinding,
  updateServerAgentPolicy,
  updateServerDiscordBinding,
} from "@/modules/discord/api";
import type {
  AgentPolicy,
  DiscordBinding,
  DiscordBindingInput,
  DiscordBot,
  DiscordOptions,
} from "@/modules/discord/types";

function revalidateDiscord(serverId?: number) {
  revalidatePath("/settings/discord");
  if (serverId != null) revalidatePath(`/servers/${serverId}/discord`);
}

export async function refreshDiscordAction(): Promise<ApiResult<DiscordBot>> {
  return getDiscordBot();
}

export async function saveDiscordAction(input: {
  readonly token?: string;
  readonly enabled?: boolean;
  readonly messageTriggerMode?: "mention_only" | "mention_and_greetings";
}): Promise<ApiResult<DiscordBot>> {
  const result = await updateDiscordBot(input);
  if (result.ok) revalidateDiscord();
  return result;
}

export async function removeDiscordAction(): Promise<
  ApiResult<{ success: boolean; message: string }>
> {
  const result = await deleteDiscordBot();
  if (result.ok) revalidateDiscord();
  return result;
}

export async function testDiscordAction(
  token?: string,
): Promise<ApiResult<{ success: boolean; username: string | null; message: string }>> {
  return testDiscordBot(token);
}

export async function refreshDiscordGlobalAction(): Promise<ApiResult<DiscordBinding>> {
  return getDiscordGlobalBinding();
}

export async function saveDiscordGlobalAction(
  input: DiscordBindingInput,
): Promise<ApiResult<DiscordBinding>> {
  const result = await updateDiscordGlobalBinding(input);
  if (result.ok) revalidateDiscord();
  return result;
}

export async function refreshDiscordGlobalOptionsAction(
  guildId?: string,
): Promise<ApiResult<DiscordOptions>> {
  return getDiscordGlobalOptions(guildId);
}

export async function refreshDiscordMenuOptionsAction(
  guildId?: string,
): Promise<ApiResult<DiscordOptions>> {
  return getDiscordMenuOptions(guildId);
}

export async function pushDiscordMenuAction(
  guildId: string,
  channelId: string,
): Promise<ApiResult<{ messageId: string }>> {
  return pushDiscordMenu(guildId, channelId);
}

export async function saveServerDiscordAction(
  serverId: number,
  input: DiscordBindingInput,
): Promise<ApiResult<DiscordBinding>> {
  const result = await updateServerDiscordBinding(serverId, input);
  if (result.ok) revalidateDiscord(serverId);
  return result;
}

export async function refreshServerDiscordOptionsAction(
  serverId: number,
  guildId?: string,
): Promise<ApiResult<DiscordOptions>> {
  return getServerDiscordOptions(serverId, guildId);
}

export async function saveServerAgentPolicyAction(
  serverId: number,
  enabled: boolean,
  capabilities: readonly string[],
): Promise<ApiResult<AgentPolicy>> {
  const result = await updateServerAgentPolicy(serverId, enabled, capabilities);
  if (result.ok) revalidateDiscord(serverId);
  return result;
}

export async function refreshServerDiscordAction(
  serverId: number,
): Promise<ApiResult<DiscordBinding>> {
  return getServerDiscordBinding(serverId);
}

export async function refreshServerAgentPolicyAction(
  serverId: number,
): Promise<ApiResult<AgentPolicy>> {
  return getServerAgentPolicy(serverId);
}
