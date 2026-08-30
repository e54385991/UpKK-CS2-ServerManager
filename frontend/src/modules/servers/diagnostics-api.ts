import "server-only";
import { apiFetch, type ApiResult } from "@/shared/api/server-fetch";

export type DiagnosticScope = "metamod" | "counterstrikesharp" | "both";

export type DiagnosticRecommendation = {
  readonly recommended: boolean;
  readonly reason: string | null;
  readonly recentlyUpdated: boolean;
  readonly restartCount: number;
  readonly maxRestarts: number;
  readonly windowMinutes: number;
};

export type DiagnosticPlan = {
  readonly serverId: number;
  readonly scope: string;
  readonly planHash: string;
  readonly estimatedMaxStarts: number;
  readonly warnings: readonly string[];
};

export type DiagnosticRun = {
  readonly id: string;
  readonly status: string;
  readonly planHash: string;
  readonly error: string | null;
};

type RecommendationDto = {
  recommended: boolean;
  reason?: string | null;
  recently_updated: boolean;
  restart_count: number;
  max_restarts: number;
  window_minutes: number;
};

type PlanDto = {
  server_id: number;
  scope: string;
  plan_hash: string;
  estimated_max_starts: number;
  warnings?: string[];
};

type RunDto = {
  id: string;
  status: string;
  plan_hash: string;
  error?: string | null;
};

export async function getDiagnosticRecommendation(
  serverId: number,
): Promise<ApiResult<DiagnosticRecommendation>> {
  const result = await apiFetch<RecommendationDto>(
    `/api/v1/servers/${serverId}/plugin-diagnostics/recommendation`,
  );
  if (!result.ok) return result;
  return {
    ok: true,
    data: {
      recommended: result.data.recommended,
      reason: result.data.reason ?? null,
      recentlyUpdated: result.data.recently_updated,
      restartCount: result.data.restart_count,
      maxRestarts: result.data.max_restarts,
      windowMinutes: result.data.window_minutes,
    },
  };
}

export async function planPluginDiagnostic(
  serverId: number,
  scope: DiagnosticScope,
): Promise<ApiResult<DiagnosticPlan>> {
  const result = await apiFetch<PlanDto>(
    `/api/v1/servers/${serverId}/plugin-diagnostics/plan`,
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ scope }),
    },
  );
  if (!result.ok) return result;
  return {
    ok: true,
    data: {
      serverId: result.data.server_id,
      scope: result.data.scope,
      planHash: result.data.plan_hash,
      estimatedMaxStarts: result.data.estimated_max_starts,
      warnings: result.data.warnings ?? [],
    },
  };
}

export async function executePluginDiagnostic(
  serverId: number,
  scope: DiagnosticScope,
  expectedPlanHash: string,
): Promise<ApiResult<DiagnosticRun>> {
  const result = await apiFetch<RunDto>(
    `/api/v1/servers/${serverId}/plugin-diagnostics/runs`,
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        scope,
        expected_plan_hash: expectedPlanHash,
      }),
    },
  );
  if (!result.ok) return result;
  return {
    ok: true,
    data: {
      id: result.data.id,
      status: result.data.status,
      planHash: result.data.plan_hash,
      error: result.data.error ?? null,
    },
  };
}

export async function restorePluginDiagnostic(
  serverId: number,
  diagnosticId: string,
): Promise<ApiResult<DiagnosticRun>> {
  const result = await apiFetch<RunDto>(
    `/api/v1/servers/${serverId}/plugin-diagnostics/runs/${diagnosticId}/restore`,
    { method: "POST" },
  );
  if (!result.ok) return result;
  return {
    ok: true,
    data: {
      id: result.data.id,
      status: result.data.status,
      planHash: result.data.plan_hash,
      error: result.data.error ?? null,
    },
  };
}
