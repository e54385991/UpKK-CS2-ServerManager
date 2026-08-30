"use server";

import { revalidatePath } from "next/cache";
import type { ApiResult } from "@/shared/api/server-fetch";
import { getConsoleWorkspace } from "@/modules/console/api";
import type { ConsoleWorkspace } from "@/modules/console/types";

export async function refreshConsoleAction(
  serverId: number,
): Promise<ApiResult<ConsoleWorkspace>> {
  const result = await getConsoleWorkspace(serverId);
  if (result.ok) revalidatePath(`/servers/${serverId}/console`);
  return result;
}
