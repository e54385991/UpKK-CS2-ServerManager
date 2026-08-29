import type { Route } from "next";
import {
  isActiveOperation,
  type ServerOperation,
  type ServerStatus,
} from "@/modules/servers/types";

export type LiveConsolePreferredView = "auto" | "deploy" | "console";
export type LiveConsoleView = "deploy" | "console";

export function parseLiveConsoleView(
  value: string | string[] | undefined,
): LiveConsolePreferredView {
  const raw = Array.isArray(value) ? value[0] : value;
  if (raw === "deploy" || raw === "console") return raw;
  return "auto";
}

export function liveConsoleHref(
  serverId: number,
  view?: Exclude<LiveConsolePreferredView, "auto">,
): Route {
  if (view === "deploy") {
    return `/live-console/${serverId}?view=deploy` as Route;
  }
  if (view === "console") {
    return `/live-console/${serverId}?view=console` as Route;
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
  if (isActiveOperation(operation)) return "deploy";
  if (preferredView === "console") return "console";
  if (
    preferredView === "deploy" ||
    serverStatus === "deploying" ||
    lockActive
  ) {
    return "deploy";
  }
  return "console";
}
