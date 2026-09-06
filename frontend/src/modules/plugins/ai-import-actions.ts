"use server";

import { revalidatePath } from "next/cache";
import { apiFetch } from "@/shared/api/server-fetch";
import type { components } from "@/shared/api/schema";

type Schema = components["schemas"];
const base = "/api/v1/plugins/market/ai-imports";

export async function aiImportReadiness() {
  return apiFetch<Schema["PluginAIReadinessView"]>(`${base}/readiness`);
}

export async function submitAIImport(body: Schema["PluginAIImportRequest"]) {
  return apiFetch<Schema["PluginAIImportView"]>(base, {
    method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body),
  });
}

export async function listAIImports() {
  return apiFetch<Schema["PluginAIImportView"][]>(base);
}

export async function getAIImport(id: string) {
  return apiFetch<Schema["PluginAIImportView"]>(`${base}/${encodeURIComponent(id)}`);
}

export async function cancelAIImport(id: string) {
  return apiFetch<Schema["PluginAIImportView"]>(`${base}/${encodeURIComponent(id)}/cancel`, { method: "POST" });
}

export async function verifyGitHubToken() {
  return apiFetch<Schema["GitHubTokenVerificationView"]>("/api/v1/settings/test-github-token", { method: "POST" });
}

export async function reviewAIPlugin(id: number, metadata: Schema["PluginAIInfo"]) {
  const result = await apiFetch<Schema["PluginAIReviewView"]>(`${base}/plugins/${id}/review`, {
    method: "PATCH", headers: { "content-type": "application/json" }, body: JSON.stringify({ metadata }),
  });
  if (result.ok) revalidatePath("/plugins");
  return result;
}
