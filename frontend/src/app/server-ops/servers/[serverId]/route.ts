import { NextRequest } from "next/server";
import {
  applyAptMirror,
  cancelOperation,
  cancelInitializedHostOperation,
  clearDeploymentLock,
  getCurrentServerOperation,
  getDeploymentLock,
  getOperationJournal,
  getServer,
  listOperationLogs,
  restoreS3Backup,
  startServerOperation,
} from "@/modules/servers/api";
import type { AptMirrorId } from "@/modules/servers/apt-mirrors";
import type { ServerOperationAction } from "@/modules/servers/types";

/**
 * Cookie → Bearer JSON proxy for operation start/progress. Browser mutations
 * cannot send the HttpOnly JWT, and hashed Server Actions go stale after a
 * Docker image pull — that remounts the workspace and looks like a freeze.
 */
export const dynamic = "force-dynamic";

function parseServerId(value: string): number | null {
  if (!/^\d+$/.test(value)) return null;
  return Number(value);
}

function resultResponse(result: { ok: boolean; status?: number }) {
  return Response.json(result, {
    status: result.ok ? 200 : result.status && result.status > 0 ? result.status : 502,
  });
}

export async function POST(
  request: NextRequest,
  context: { params: Promise<{ serverId: string }> },
) {
  const serverId = parseServerId((await context.params).serverId);
  if (serverId == null) {
    return Response.json({ ok: false, status: 400, error: "Invalid server id" }, { status: 400 });
  }
  const body = (await request.json()) as {
    intent?: string;
    action?: ServerOperationAction;
    mirror?: AptMirrorId;
    objectKey?: string;
    operationId?: string;
    initializedServerId?: number;
  };
  if (body.intent === "force-stop") {
    return resultResponse(await clearDeploymentLock(serverId));
  }
  if (body.intent === "cancel-operation" && body.operationId) {
    return resultResponse(await cancelOperation(serverId, body.operationId));
  }
  if (
    body.intent === "cancel-initialized-operation" &&
    body.operationId &&
    body.initializedServerId === serverId
  ) {
    return resultResponse(
      await cancelInitializedHostOperation(serverId, body.operationId),
    );
  }
  if (body.intent === "apt-mirror" && body.mirror) {
    return resultResponse(await applyAptMirror(serverId, body.mirror));
  }
  if (body.intent === "s3-restore" && body.objectKey) {
    return resultResponse(await restoreS3Backup(serverId, body.objectKey));
  }
  if (body.action) {
    return resultResponse(await startServerOperation(serverId, body.action));
  }
  return Response.json({ ok: false, status: 400, error: "Missing operation" }, { status: 400 });
}

export async function GET(
  request: NextRequest,
  context: { params: Promise<{ serverId: string }> },
) {
  const serverId = parseServerId((await context.params).serverId);
  if (serverId == null) {
    return Response.json({ ok: false, status: 400, error: "Invalid server id" }, { status: 400 });
  }
  const view = request.nextUrl.searchParams.get("view") ?? "current";
  if (view === "journal") {
    const operationId = request.nextUrl.searchParams.get("operationId") ?? "";
    if (!operationId) {
      return Response.json(
        { ok: false, status: 400, error: "Missing operation id" },
        { status: 400 },
      );
    }
    return resultResponse(await getOperationJournal(serverId, operationId));
  }
  if (view === "snapshot") {
    const [server, logs, lock] = await Promise.all([
      getServer(serverId),
      listOperationLogs(serverId),
      getDeploymentLock(serverId),
    ]);
    if (!server.ok) return resultResponse(server);
    return Response.json({
      ok: true,
      data: {
        server: server.data,
        logs: logs.ok ? logs.data : [],
        lock: lock.ok ? lock.data : { lockActive: false, serverStatus: server.data.status },
      },
    });
  }
  return resultResponse(await getCurrentServerOperation(serverId));
}
