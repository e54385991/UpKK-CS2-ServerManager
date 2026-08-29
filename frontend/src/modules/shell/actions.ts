"use server";

import { getSshPool } from "@/modules/shell/api";
import type { SshPoolStats } from "@/modules/shell/types";
import type { ApiResult } from "@/shared/api/server-fetch";

export async function refreshSshPoolAction(): Promise<ApiResult<SshPoolStats>> {
  return getSshPool();
}
