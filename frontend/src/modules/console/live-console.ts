import type { Route } from "next";
import {
  isActiveOperation,
  type ServerOperation,
  type ServerStatus,
} from "@/modules/servers/types";

export type LiveConsolePreferredView =
  | "auto"
  | "deploy"
  | "ssh"
  | "game"
  | "console";
export type LiveConsoleView = "deploy" | "ssh" | "game" | "launcher";
export type LiveTerminalView = "deploy" | "ssh" | "game";

export function parseLiveConsoleView(
  value: string | string[] | undefined,
): LiveConsolePreferredView {
  const raw = Array.isArray(value) ? value[0] : value;
  if (
    raw === "deploy" ||
    raw === "ssh" ||
    raw === "game" ||
    raw === "console"
  ) {
    return raw;
  }
  return "auto";
}

export function liveConsoleHref(
  serverId: number,
  view?: LiveTerminalView | "console",
): Route {
  if (view === "deploy" || view === "ssh" || view === "game") {
    return `/live-console/${serverId}?view=${view}` as Route;
  }
  if (view === "console") {
    return `/live-console/${serverId}` as Route;
  }
  return `/live-console/${serverId}` as Route;
}

export function resolveLiveConsoleView({
  preferredView,
  operation,
  serverStatus,
  lockActive,
}: {
  preferredView: LiveConsolePreferredView;
  operation: ServerOperation | null;
  serverStatus: ServerStatus | null;
  lockActive: boolean;
}): LiveConsoleView {
  if (preferredView === "ssh" || preferredView === "game") {
    return preferredView;
  }
  if (isActiveOperation(operation)) return "deploy";
  if (
    preferredView === "deploy" ||
    serverStatus === "deploying" ||
    lockActive
  ) {
    return "deploy";
  }
  return "launcher";
}
