"use server";

import { apiFetch } from "@/shared/api/server-fetch";
import type { components } from "@/shared/api/schema";

type Schema = components["schemas"];
const base = "/api/v1/plugins/market/releases/sync";

export async function listMarketReleaseSyncs(mode?: "sync" | "scan") {
  const query = mode ? `?mode=${encodeURIComponent(mode)}` : "";
  return apiFetch<Schema["MarketPluginReleaseSyncView"][]>(`${base}${query}`);
}

export async function getMarketReleaseSync(operationId: string) {
  return apiFetch<Schema["MarketPluginReleaseSyncView"]>(
    `${base}/${encodeURIComponent(operationId)}`,
  );
}

export async function cancelMarketReleaseSync(operationId: string) {
  return apiFetch<Schema["MarketPluginReleaseSyncView"]>(
    `${base}/${encodeURIComponent(operationId)}/cancel`,
    { method: "POST" },
  );
}

export async function deleteMarketReleaseSync(operationId: string) {
  return apiFetch<Schema["ActionResult"]>(
    `${base}/${encodeURIComponent(operationId)}`,
    { method: "DELETE" },
  );
}
