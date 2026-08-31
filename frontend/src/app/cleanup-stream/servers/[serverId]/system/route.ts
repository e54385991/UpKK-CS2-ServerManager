import { cookies } from "next/headers";
import type { NextRequest } from "next/server";
import { sessionTokenFrom } from "@/modules/auth/session";
import { internalApiUrl } from "@/shared/config/internal-api";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

function pipeUnbuffered(
  body: ReadableStream<Uint8Array>,
): ReadableStream<Uint8Array> {
  const reader = body.getReader();
  return new ReadableStream({
    async pull(controller) {
      const { done, value } = await reader.read();
      if (done) {
        controller.close();
        return;
      }
      controller.enqueue(value);
    },
    cancel(reason) {
      return reader.cancel(reason);
    },
  });
}

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
    `${internalApiUrl()}/api/v1/servers/${serverId}/cleanup/system/events`,
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
