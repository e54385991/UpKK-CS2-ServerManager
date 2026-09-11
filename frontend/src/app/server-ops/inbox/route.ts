import { NextRequest } from "next/server";
import {
  clearCompletedOperations,
  dismissCompletedOperation,
} from "@/modules/servers/completed-operation-api";
import {
  clearFailedOperations,
  dismissFailedOperation,
  listOperationInbox,
} from "@/modules/servers/api";

/**
 * Cookie → Bearer JSON proxy for the activity tray. Inbox polling used hashed
 * Server Actions, which fail after a Docker pull and remount the console.
 */
export const dynamic = "force-dynamic";

function resultResponse(result: { ok: boolean; status?: number }) {
  return Response.json(result, {
    status: result.ok ? 200 : result.status && result.status > 0 ? result.status : 502,
  });
}

export async function GET() {
  return resultResponse(await listOperationInbox());
}

export async function DELETE(request: NextRequest) {
  const history = request.nextUrl.searchParams.get("history");
  const operationId = request.nextUrl.searchParams.get("operationId");
  if (history === "completed") {
    if (operationId) {
      return resultResponse(await dismissCompletedOperation(operationId));
    }
    return resultResponse(await clearCompletedOperations());
  }
  if (operationId) {
    return resultResponse(await dismissFailedOperation(operationId));
  }
  return resultResponse(await clearFailedOperations());
}
