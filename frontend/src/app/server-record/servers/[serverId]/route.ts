import { cookies } from "next/headers";
import type { NextRequest } from "next/server";
import { sessionTokenFrom } from "@/modules/auth/session";
import { internalApiUrl } from "@/shared/config/internal-api";

/**
 * Cookie → Bearer proxy for deleting a panel server record.
 * Browser fetch cannot attach the HttpOnly JWT.
 */
export async function DELETE(
  _request: NextRequest,
  context: { params: Promise<{ serverId: string }> },
) {
  const token = sessionTokenFrom(await cookies());
  if (!token) {
    return Response.json({ detail: "Authentication required" }, { status: 401 });
  }

  const { serverId } = await context.params;
  if (!/^\d+$/.test(serverId)) {
    return Response.json({ detail: "Invalid server id" }, { status: 400 });
  }

  try {
    const upstream = await fetch(
      `${internalApiUrl()}/api/v1/servers/${serverId}`,
      {
        method: "DELETE",
        headers: {
          accept: "application/json",
          authorization: `Bearer ${token}`,
        },
        cache: "no-store",
        signal: AbortSignal.timeout(20_000),
      },
    );
    return new Response(await upstream.text(), {
      status: upstream.status,
      headers: {
        "content-type":
          upstream.headers.get("content-type") ?? "application/json",
      },
    });
  } catch (error) {
    const detail = error instanceof Error ? error.message : "upstream unavailable";
    return Response.json({ detail }, { status: 502 });
  }
}
