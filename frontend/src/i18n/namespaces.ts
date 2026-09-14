import type { MessageNamespace } from "@/i18n/pick-messages";

export const ROOT_NAMESPACES = ["feedback"] as const satisfies readonly MessageNamespace[];

export const LOGIN_CLIENT_NAMESPACES = [
  "feedback",
  "login",
] as const satisfies readonly MessageNamespace[];

export const REGISTER_CLIENT_NAMESPACES = [
  "feedback",
  "register",
] as const satisfies readonly MessageNamespace[];

export const FORGOT_PASSWORD_CLIENT_NAMESPACES = [
  "feedback",
  "forgotPassword",
] as const satisfies readonly MessageNamespace[];

export const RESET_PASSWORD_CLIENT_NAMESPACES = [
  "feedback",
  "resetPassword",
] as const satisfies readonly MessageNamespace[];

export const CONSOLE_NAMESPACES = [
  "feedback",
  "site",
  "nav",
  "shell",
  "serverDetail",
  "plugins.aiImport",
] as const satisfies readonly MessageNamespace[];

export const LIVE_CONSOLE_NAMESPACES = [
  "feedback",
  "console",
  "serverDetail",
] as const satisfies readonly MessageNamespace[];

export const WORKSPACE_NAMESPACES = [
  ...CONSOLE_NAMESPACES,
  "servers",
  "serverWorkspace",
  "console",
  "startupCommand",
  "files",
  "maps",
  "plugins",
  "pluginConfigs",
  "pluginUpdates",
  "quickCommands",
  "gameUpdates",
  "cleanup",
  "schedule",
  "discord",
  "serverHelp",
  "gameModes",
  "serverAdditionalFixes",
  "s3Backups",
  "serverMonitoring",
  "serverConfig",
  "profile",
] as const satisfies readonly MessageNamespace[];

export const SERVERS_SECTION_NAMESPACES = [
  ...CONSOLE_NAMESPACES,
  "servers",
  "serverNew",
  "setupWizard",
  "initializedHosts",
] as const satisfies readonly MessageNamespace[];

export const SETTINGS_TREE_NAMESPACES = [
  ...CONSOLE_NAMESPACES,
  "settings",
  "aiSettings",
  "profile",
  "discord",
] as const satisfies readonly MessageNamespace[];

export const OVERVIEW_CLIENT_NAMESPACES = CONSOLE_NAMESPACES;

export function withConsoleNamespaces(
  ...extra: readonly MessageNamespace[]
): MessageNamespace[] {
  return [...CONSOLE_NAMESPACES, ...extra];
}
