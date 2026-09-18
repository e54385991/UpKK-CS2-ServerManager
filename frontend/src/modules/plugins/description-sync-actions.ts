"use server";

import { apiFetch } from "@/shared/api/server-fetch";
import type { components } from "@/shared/api/schema";

type Schema = components["schemas"];
const base = "/api/v1/plugins/market/descriptions/sync";

export async function getMarketDescriptionSync(operationId: string) {
  return apiFetch<Schema["MarketPluginDescriptionSyncView"]>(
    `${base}/${encodeURIComponent(operationId)}`,
  );
}

export async function cancelMarketDescriptionSync(operationId: string) {
  return apiFetch<Schema["MarketPluginDescriptionSyncView"]>(
    `${base}/${encodeURIComponent(operationId)}/cancel`,
    { method: "POST" },
  );
}

export async function deleteMarketDescriptionSync(operationId: string) {
  return apiFetch<Schema["ActionResult"]>(
    `${base}/${encodeURIComponent(operationId)}`,
    { method: "DELETE" },
  );
}
