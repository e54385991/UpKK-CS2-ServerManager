import { cookies } from "next/headers";
import type { NextRequest } from "next/server";
import { sessionTokenFrom } from "@/modules/auth/session";
import { internalApiUrl } from "@/shared/config/internal-api";
import { pipeUnbuffered } from "@/shared/server/stream-proxy";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

/**
 * Cookie → Bearer NDJSON proxy. The browser cannot attach Authorization to a
 * long-lived scan stream; this route upgrades the session cookie and forwards
 * the versioned plugin-config scan events.
 */
export async function POST(
  request: NextRequest,
  context: { params: Promise<{ serverId: string; sourceId: string }> },
) {
  const token = sessionTokenFrom(await cookies());
  if (!token) {
    return new Response("Authentication required", { status: 401 });
  }

  const { serverId, sourceId } = await context.params;
  const upstream = await fetch(
    `${internalApiUrl()}/api/v1/servers/${serverId}/plugin-configs/sources/${sourceId}/scan`,
    {
      method: "POST",
      headers: {
        accept: "application/x-ndjson",
        authorization: `Bearer ${token}`,
      },
      cache: "no-store",
      signal: request.signal,
    },
  );

  if (!upstream.ok || !upstream.body) {
    return new Response(await upstream.text(), {
      status: upstream.status,
      headers: {
        "content-type": upstream.headers.get("content-type") ?? "text/plain",
      },
    });
  }

  return new Response(pipeUnbuffered(upstream.body), {
    status: 200,
    headers: {
      "content-type": "application/x-ndjson",
      "cache-control": "no-cache, no-transform",
      "x-accel-buffering": "no",
    },
  });
}
