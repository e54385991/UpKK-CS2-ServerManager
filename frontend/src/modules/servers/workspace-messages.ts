import { CONSOLE_NAMESPACES } from "../../i18n/namespaces.ts";
import type { MessageNamespace } from "@/i18n/pick-messages";
import type { ServerWorkspaceCategory } from "./workspace";

// The frame needs its shared controls, while page-specific dictionaries stay
// with their page. Nested providers replace messages, so every page includes
// these shared namespaces as well.
export const WORKSPACE_FRAME_NAMESPACES = [
  ...CONSOLE_NAMESPACES,
  "servers",
  "serverWorkspace",
  "console",
] as const satisfies readonly MessageNamespace[];

const PAGE_NAMESPACES = {
  overview: ["startupCommand"],
  operations: ["quickCommands"],
  config: ["serverConfig", "profile"],
  frameworks: [],
  "game-modes": ["gameModes"],
  backups: ["s3Backups"],
  plugins: ["plugins"],
  "plugin-configs": ["pluginConfigs"],
  updates: ["pluginUpdates", "gameUpdates", "quickCommands"],
  maps: ["maps"],
  "host-config": ["serverConfig", "profile"],
  monitoring: ["serverMonitoring"],
  files: ["files"],
  cleanup: ["cleanup"],
  console: [],
  schedule: ["schedule"],
  discord: ["discord"],
  help: ["serverHelp"],
  "additional-fixes": ["serverAdditionalFixes"],
} as const satisfies Record<ServerWorkspaceCategory, readonly MessageNamespace[]>;

export function workspacePageNamespaces(
  category: ServerWorkspaceCategory,
): readonly MessageNamespace[] {
  return [...WORKSPACE_FRAME_NAMESPACES, ...PAGE_NAMESPACES[category]];
}
