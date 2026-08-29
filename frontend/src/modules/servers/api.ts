import "server-only";
import { apiFetch, type ApiResult } from "@/shared/api/server-fetch";
import type { ServerStatus, ServerSummary } from "@/modules/servers/types";

/**
 * Raw backend server shape. The current backend serves the full record on
 * `GET /servers`; we map it to the non-secret {@link ServerSummary} here so the
 * rest of the app depends only on the safe projection. When `/api/v1` lands,
 * only this adapter changes.
 */
type RawServer = {
  id: number;
  name: string;
  host: string;
  game_port: number;
  status: string;
  description: string | null;
  default_map: string;
  max_players: number;
};

const KNOWN_STATUSES: readonly ServerStatus[] = [
  "pending",
  "deploying",
  "running",
  "stopped",
  "error",
  "unknown",
];

function toStatus(value: string): ServerStatus {
  return (KNOWN_STATUSES as readonly string[]).includes(value)
    ? (value as ServerStatus)
    : "unknown";
}

function toSummary(raw: RawServer): ServerSummary {
  return {
    id: raw.id,
    name: raw.name,
    host: raw.host,
    gamePort: raw.game_port,
    status: toStatus(raw.status),
    description: raw.description ?? null,
    defaultMap: raw.default_map,
    maxPlayers: raw.max_players,
  };
}

export async function listServers(): Promise<ApiResult<ServerSummary[]>> {
  const result = await apiFetch<RawServer[]>("/servers");
  if (!result.ok) return result;
  return { ok: true, data: result.data.map(toSummary) };
}
