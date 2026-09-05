import type { Route } from "next";
import type { ServerListScope, ServerStatus } from "@/modules/servers/types";

export const SERVER_WORKSPACE_NAV_ROWS = [
  {
    id: "game",
    categories: [
      "overview",
      "operations",
      "config",
      "frameworks",
      "game-modes",
      "backups",
      "plugins",
      "plugin-configs",
      "updates",
      "maps",
    ],
  },
  {
    id: "host",
    categories: [
      "host-config",
      "monitoring",
      "files",
      "cleanup",
      "console",
      "schedule",
      "discord",
      "help",
      "additional-fixes",
    ],
  },
] as const;

export const SERVER_WORKSPACE_CATEGORIES = [
  ...SERVER_WORKSPACE_NAV_ROWS[0].categories,
  ...SERVER_WORKSPACE_NAV_ROWS[1].categories,
] as const;

export type ServerWorkspaceCategory =
  (typeof SERVER_WORKSPACE_CATEGORIES)[number];

export type ServerWorkspaceNavRowId =
  (typeof SERVER_WORKSPACE_NAV_ROWS)[number]["id"];

export const SERVER_STATUS_GROUPS = [
  "running",
  "deploying",
  "pending",
  "stopped",
  "error",
  "unknown",
] as const;

export function parseServerId(value: string): number | null {
  const id = Number(value);
  return Number.isInteger(id) && id > 0 ? id : null;
}

export function serversHref(input: {
  status?: ServerStatus;
  scope?: ServerListScope;
}): Route {
  const params = new URLSearchParams();
  if (input.status) params.set("status", input.status);
  if (input.scope === "all") params.set("scope", "all");
  const query = params.toString();
  return (query ? `/servers?${query}` : "/servers") as Route;
}

/**
 * Workspace nav renders ~17 in-viewport links. Default `<Link>` prefetch would
 * open that many RSC requests at once; files/cleanup also hit SSH. Together
 * with the always-on inbox EventSource this fills Chrome's 6 HTTP/1.1 sockets
 * per host, so the tab looks frozen while a fresh incognito window works.
 */
export const WORKSPACE_NAV_PREFETCH = false;

export function workspaceHref(
  serverId: number,
  category: ServerWorkspaceCategory,
): Route {
  if (category === "overview") {
    return `/servers/${serverId}` as Route;
  }
  return `/servers/${serverId}/${category}` as Route;
}

export function isWorkspaceCategoryActive(
  pathname: string,
  serverId: number,
  category: ServerWorkspaceCategory,
): boolean {
  const href = workspaceHref(serverId, category);
  if (category === "overview") {
    return pathname === href;
  }
  return pathname === href || pathname.startsWith(`${href}/`);
}
