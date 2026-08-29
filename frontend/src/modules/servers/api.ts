import "server-only";
import { apiFetch, type ApiResult } from "@/shared/api/server-fetch";
import type {
  ServerSummaryDto,
  ServerDetailDto,
  OverviewSummaryDto,
} from "@/shared/api/types";
import type { ServerStatus, ServerSummary } from "@/modules/servers/types";

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

/**
 * Map the wire DTO (snake_case, from the generated OpenAPI schema) to the
 * camelCase domain type the UI consumes. Keeping this adapter isolates the UI
 * from wire-format details; if the `/api/v1` contract changes, only this
 * mapper and the regenerated schema move.
 */
function toSummary(raw: ServerSummaryDto): ServerSummary {
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
  const result = await apiFetch<ServerSummaryDto[]>("/api/v1/servers");
  if (!result.ok) return result;
  return { ok: true, data: result.data.map(toSummary) };
}

export type ServerDetail = ServerSummary & {
  readonly sshPort: number;
  readonly sshUser: string;
  readonly gameDirectory: string;
  readonly gameMode: string;
  readonly gameType: string;
  readonly createdAt: string;
  readonly updatedAt: string;
  readonly lastDeployed: string | null;
};

function toDetail(raw: ServerDetailDto): ServerDetail {
  return {
    ...toSummary(raw),
    sshPort: raw.ssh_port,
    sshUser: raw.ssh_user,
    gameDirectory: raw.game_directory,
    gameMode: raw.game_mode,
    gameType: raw.game_type,
    createdAt: raw.created_at,
    updatedAt: raw.updated_at,
    lastDeployed: raw.last_deployed ?? null,
  };
}

export async function getServer(
  id: number,
): Promise<ApiResult<ServerDetail>> {
  const result = await apiFetch<ServerDetailDto>(`/api/v1/servers/${id}`);
  if (!result.ok) return result;
  return { ok: true, data: toDetail(result.data) };
}

export type OverviewSummary = {
  readonly total: number;
  readonly running: number;
  readonly attention: number;
  readonly capacity: number;
};

export async function getOverviewSummary(): Promise<
  ApiResult<OverviewSummary>
> {
  const result = await apiFetch<OverviewSummaryDto>("/api/v1/overview/summary");
  if (!result.ok) return result;
  const { total, running, attention, capacity } = result.data;
  return { ok: true, data: { total, running, attention, capacity } };
}
