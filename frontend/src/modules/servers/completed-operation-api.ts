import "server-only";

import type { ActionResultDto } from "@/shared/api/types";
import { apiFetch, type ApiResult } from "@/shared/api/server-fetch";

export async function clearCompletedOperations(): Promise<ApiResult<ActionResultDto>> {
  return apiFetch<ActionResultDto>("/api/v1/operations/inbox/completed", {
    method: "DELETE",
  });
}

export async function dismissCompletedOperation(
  operationId: string,
): Promise<ApiResult<ActionResultDto>> {
  return apiFetch<ActionResultDto>(
    `/api/v1/operations/inbox/completed/${operationId}`,
    { method: "DELETE" },
  );
}
