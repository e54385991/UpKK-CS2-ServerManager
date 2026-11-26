"use server";

import { revalidatePublicPath } from "@/i18n/revalidate";
import { mapServerOperation } from "@/modules/servers/operation-inbox";
import type {
  MarketReleaseDeployRequestDto,
  MarketReleaseDeployViewDto,
  MarketReleasePreviewRequestDto,
  MarketReleasePreviewViewDto,
  MarketReleaseTargetsViewDto,
} from "@/shared/api/types";
import { apiFetch, type ApiResult } from "@/shared/api/server-fetch";
import type { ServerOperation } from "@/modules/servers/types";

const endpoint = (id: number) => `/api/v1/plugins/market/${id}/release-sync`;

function post(body: object) {
  return {
    method: "POST" as const,
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
    timeoutMs: 120_000,
  };
}

export async function previewMarketRelease(
  id: number,
  input: MarketReleasePreviewRequestDto,
) {
  return apiFetch<MarketReleasePreviewViewDto>(`${endpoint(id)}/preview`, post(input));
}

export async function saveMarketRelease(id: number, previewId: string) {
  const result = await apiFetch<MarketReleaseTargetsViewDto>(
    endpoint(id),
    post({ preview_id: previewId }),
  );
  if (result.ok) {
    revalidatePublicPath("/plugins");
    revalidatePublicPath(`/plugins/${id}`);
  }
  return result;
}

export async function listMarketReleaseTargets(id: number) {
  return apiFetch<MarketReleaseTargetsViewDto>(`${endpoint(id)}/targets`);
}

export async function deployMarketRelease(
  id: number,
  input: MarketReleaseDeployRequestDto,
): Promise<ApiResult<Array<{
  serverId: number;
  serverName: string | null;
  error: string | null;
  operation: ServerOperation | null;
}>>> {
  const result = await apiFetch<MarketReleaseDeployViewDto>(
    `${endpoint(id)}/deploy`,
    post(input),
  );
  if (!result.ok) return result;
  return {
    ok: true,
    data: result.data.items.map((item) => ({
      serverId: item.server_id,
      serverName: item.server_name ?? null,
      error: item.error ?? null,
      operation: item.operation
        ? mapServerOperation(item.operation)
        : null,
    })),
  };
}
