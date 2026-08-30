import type { ConsolePane, ConsolePaneKind } from "@/modules/console/types";

type ConsolePaneViewDto = {
  server_id: number;
  kind: "game" | "steamcmd";
  session_name: string;
  session_manager?: "screen" | "tmux" | null;
  ssh_ok: boolean;
  running?: boolean;
  text?: string;
  heartbeat?: string | null;
  message?: string | null;
};

function toPane(raw: ConsolePaneViewDto): ConsolePane {
  return {
    serverId: raw.server_id,
    kind: raw.kind === "steamcmd" ? "steamcmd" : "game",
    sessionName: raw.session_name,
    sessionManager:
      raw.session_manager === "screen" || raw.session_manager === "tmux"
        ? raw.session_manager
        : null,
    sshOk: raw.ssh_ok,
    running: Boolean(raw.running),
    text: raw.text ?? "",
    heartbeat: raw.heartbeat ?? null,
    message: raw.message ?? null,
  };
}

export function paneDisplayText(pane: ConsolePane | null): string {
  if (!pane) return "";
  if (pane.text.trim()) return pane.text;
  return pane.heartbeat ?? "";
}

export async function fetchConsolePane(
  serverId: number,
  kind: ConsolePaneKind,
): Promise<ConsolePane | null> {
  const response = await fetch(
    `/api/v1/servers/${serverId}/console/pane?kind=${kind}`,
    { cache: "no-store", credentials: "same-origin" },
  );
  if (!response.ok) return null;
  const raw = (await response.json()) as ConsolePaneViewDto;
  return toPane(raw);
}
