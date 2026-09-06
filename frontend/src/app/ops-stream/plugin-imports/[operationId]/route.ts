import { cookies } from "next/headers";
import type { NextRequest } from "next/server";
import { sessionTokenFrom } from "@/modules/auth/session";
import { internalApiUrl } from "@/shared/config/internal-api";
import { pipeUnbuffered } from "@/shared/server/stream-proxy";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

/**
 * Cookie → Bearer SSE proxy for the global operation inbox. EventSource cannot
 * set Authorization; the tray uses this route instead of a WebSocket.
 */
export async function GET(request: NextRequest, context: { params: Promise<{ operationId: string }> }) {
  const { operationId } = await context.params;
  const token = sessionTokenFrom(await cookies());
  if (!token) {
    return new Response("Authentication required", { status: 401 });
  }

  const upstream = await fetch(
    `${internalApiUrl()}/api/v1/plugins/market/ai-imports/${encodeURIComponent(operationId)}/events`,
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
      headers: {
        "content-type": upstream.headers.get("content-type") ?? "text/plain",
      },
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
