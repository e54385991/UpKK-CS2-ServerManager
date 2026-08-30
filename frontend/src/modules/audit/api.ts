import "server-only";
import { apiFetch, type ApiResult } from "@/shared/api/server-fetch";
import type { AuditEntryDto, AuditPageDto } from "@/shared/api/types";
import type { AuditEntry } from "@/modules/audit/types";

export type AuditQuery = {
  readonly category?: string;
  readonly status?: string;
  readonly username?: string;
  readonly limit?: number;
  readonly offset?: number;
};

export type AuditPage = {
  readonly items: AuditEntry[];
  readonly total: number;
  readonly limit: number;
  readonly offset: number;
};

function toEntry(raw: AuditEntryDto): AuditEntry {
  return {
    id: raw.id,
    createdAt: raw.created_at ?? null,
    category: raw.category,
    action: raw.action,
    status: raw.status,
    actorUsername: raw.actor_username ?? null,
    ipAddress: raw.ip_address ?? null,
    source: raw.source,
    serverId: raw.server_id ?? null,
  };
}

export async function listAudit(
  query: AuditQuery,
): Promise<ApiResult<AuditPage>> {
  const params = new URLSearchParams();
  if (query.category) params.set("category", query.category);
  if (query.status) params.set("status", query.status);
  if (query.username) params.set("username", query.username);
  params.set("limit", String(query.limit ?? 25));
  params.set("offset", String(query.offset ?? 0));

  const result = await apiFetch<AuditPageDto>(`/api/v1/audit?${params}`);
  if (!result.ok) return result;
  return {
    ok: true,
    data: {
      items: result.data.items.map(toEntry),
      total: result.data.total,
      limit: result.data.limit,
      offset: result.data.offset,
    },
  };
}
