export type DiscordBot = {
  readonly enabled: boolean;
  readonly tokenConfigured: boolean;
  readonly messageTriggerMode: "mention_only" | "mention_and_greetings";
  readonly username: string | null;
  readonly connectionStatus: string;
  readonly lastError: string | null;
  readonly inviteUrl: string | null;
};

export const DISCORD_CAPABILITIES = [
  "status",
  "start",
  "stop",
  "restart",
  "update",
  "validate",
  "plugin_browse",
  "plugin_install",
  "plugin_upgrade",
  "game_console",
  "change_map",
  "agent_ask",
] as const;

export const AGENT_CAPABILITIES = [
  "inspect_status",
  "read_logs_files",
  "browse_plan_plugins",
  "start",
  "stop",
  "restart",
  "deploy",
  "update",
  "validate",
  "manage_frameworks",
  "install_market_plugins",
  "install_or_upgrade_github_plugins",
  "upgrade_managed_plugins",
  "write_configuration",
  "manage_workshop_maps",
  "run_plugin_diagnostics",
  "send_game_console_commands",
  "change_current_map",
  "execute_saved_host_commands",
] as const;

export type DiscordCapability = (typeof DISCORD_CAPABILITIES)[number];
export type AgentCapability = (typeof AGENT_CAPABILITIES)[number];

export type DiscordOption = {
  readonly id: string;
  readonly name: string;
};

export type DiscordOptions = {
  readonly tokenConfigured: boolean;
  readonly guilds: readonly DiscordOption[];
  readonly channels: readonly DiscordOption[];
  readonly roles: readonly DiscordOption[];
  readonly message: string | null;
};

export type DiscordBinding = {
  readonly enabled: boolean;
  readonly effectiveEnabled?: boolean;
  readonly disabledReason: string | null;
  readonly guildId: string | null;
  readonly channelIds: readonly string[];
  readonly roleIds: readonly string[];
  readonly userIds: readonly string[];
  readonly allowChannelManagers: boolean;
  readonly allowServerAdministrators: boolean;
  readonly capabilities: readonly string[];
  readonly configured?: boolean;
  readonly serverCount?: number;
};

export type DiscordBindingInput = {
  readonly enabled: boolean;
  readonly guildId: string | null;
  readonly channelIds: readonly string[];
  readonly roleIds: readonly string[];
  readonly userIds: readonly string[];
  readonly allowChannelManagers: boolean;
  readonly allowServerAdministrators: boolean;
  readonly capabilities: readonly string[];
  readonly syncExistingServers?: boolean;
};

export type AgentPolicy = {
  readonly serverId: number;
  readonly enabled: boolean;
  readonly effectiveEnabled: boolean;
  readonly disabledReason: string | null;
  readonly capabilities: readonly string[];
};
