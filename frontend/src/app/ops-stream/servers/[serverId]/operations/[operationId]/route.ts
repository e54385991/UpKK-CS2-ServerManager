import { cookies } from "next/headers";
import type { NextRequest } from "next/server";
import { sessionTokenFrom } from "@/modules/auth/session";
import { internalApiUrl } from "@/shared/config/internal-api";
import { pipeUnbuffered } from "@/shared/server/stream-proxy";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

/**
 * Cookie → Bearer SSE proxy. EventSource cannot set Authorization, and the
 * FastAPI mutation surface stays Bearer-only; this route attaches the session
 * JWT and streams the versioned operation events through.
 *
 * Last-Event-ID is ignored: EventSource reconnects would skip Redis replay
 * and leave the panel on "waiting for the first progress line".
 */
export async function GET(
  request: NextRequest,
  context: {
    params: Promise<{ serverId: string; operationId: string }>;
  },
) {
  const token = sessionTokenFrom(await cookies());
  if (!token) {
    return new Response("Authentication required", { status: 401 });
  }

  const { serverId, operationId } = await context.params;
  const after = request.nextUrl.searchParams.get("after") ?? "0";

  const upstream = await fetch(
    `${internalApiUrl()}/api/v1/servers/${serverId}/operations/${operationId}/events?after=${after}`,
    {
      headers: {
        accept: "text/event-stream",
        authorization: `Bearer ${token}`,
      },
      cache: "no-store",
      signal: request.signal,
    },
  );

  if (!upstream.ok || !upstream.body) {
    return new Response(await upstream.text(), {
      status: upstream.status,
      headers: { "content-type": upstream.headers.get("content-type") ?? "text/plain" },
    });
  }

  return new Response(pipeUnbuffered(upstream.body), {
    status: 200,
    headers: {
      "content-type": "text/event-stream",
      "cache-control": "no-cache, no-transform",
      connection: "keep-alive",
      "x-accel-buffering": "no",
    },
  });
}
