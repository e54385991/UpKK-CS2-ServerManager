import { cookies } from "next/headers";
import type { NextRequest } from "next/server";
import { SESSION_COOKIE } from "@/modules/auth/session";
import { internalApiUrl } from "@/shared/config/internal-api";

export async function GET(
  request: NextRequest,
  context: { params: Promise<{ runId: string }> },
) {
  const token = (await cookies()).get(SESSION_COOKIE)?.value;
  if (!token) {
    return new Response("Authentication required", { status: 401 });
  }

  const { runId } = await context.params;
  const incoming = request.nextUrl.searchParams;
  const lastEventId = request.headers.get("last-event-id");
  const after = incoming.get("after") ?? (lastEventId?.match(/^\d+$/) ? lastEventId : "0");

  const upstream = await fetch(
    `${internalApiUrl()}/api/v1/assistant/runs/${runId}/events?after=${after}`,
    {
      headers: {
        accept: "text/event-stream",
        authorization: `Bearer ${token}`,
        ...(lastEventId ? { "last-event-id": lastEventId } : {}),
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

  return new Response(upstream.body, {
    status: 200,
    headers: {
      "content-type": "text/event-stream",
      "cache-control": "no-cache, no-transform",
      connection: "keep-alive",
      "x-accel-buffering": "no",
    },
  });
}
