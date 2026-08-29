"use server";

import { revalidatePath } from "next/cache";
import type { ApiResult } from "@/shared/api/server-fetch";
import {
  executePluginDiagnostic,
  getDiagnosticRecommendation,
  planPluginDiagnostic,
  restorePluginDiagnostic,
  type DiagnosticPlan,
  type DiagnosticRecommendation,
  type DiagnosticRun,
  type DiagnosticScope,
} from "@/modules/servers/diagnostics-api";

export async function getDiagnosticRecommendationAction(
  serverId: number,
): Promise<ApiResult<DiagnosticRecommendation>> {
  return getDiagnosticRecommendation(serverId);
}

export async function planPluginDiagnosticAction(
  serverId: number,
  scope: DiagnosticScope,
): Promise<ApiResult<DiagnosticPlan>> {
  return planPluginDiagnostic(serverId, scope);
}

export async function executePluginDiagnosticAction(
  serverId: number,
  scope: DiagnosticScope,
  expectedPlanHash: string,
): Promise<ApiResult<DiagnosticRun>> {
  const result = await executePluginDiagnostic(serverId, scope, expectedPlanHash);
  if (result.ok) revalidatePath(`/servers/${serverId}/monitoring`);
  return result;
}

export async function restorePluginDiagnosticAction(
  serverId: number,
  diagnosticId: string,
): Promise<ApiResult<DiagnosticRun>> {
  const result = await restorePluginDiagnostic(serverId, diagnosticId);
  if (result.ok) revalidatePath(`/servers/${serverId}/monitoring`);
  return result;
}
