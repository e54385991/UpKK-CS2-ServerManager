import { cookies } from "next/headers";
import type { NextRequest } from "next/server";
import { sessionTokenFrom } from "@/modules/auth/session";
import { internalApiUrl } from "@/shared/config/internal-api";
import { pipeUnbuffered } from "@/shared/server/stream-proxy";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

/** Cookie → Bearer SSE proxy. EventSource cannot set Authorization. */
export async function GET(
  request: NextRequest,
  context: { params: Promise<{ serverId: string }> },
) {
  const token = sessionTokenFrom(await cookies());
  if (!token) {
    return new Response("Authentication required", { status: 401 });
  }

  const { serverId } = await context.params;
  const upstream = await fetch(
    `${internalApiUrl()}/api/v1/servers/${serverId}/cleanup/scan/events`,
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
