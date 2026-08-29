import { cookies } from "next/headers";
import type { NextRequest } from "next/server";
import { SESSION_COOKIE } from "@/modules/auth/session";
import { internalApiUrl } from "@/shared/config/internal-api";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";
export const maxDuration = 1800;

/**
 * Cookie → Bearer proxy for host auto-setup. The browser keeps a WebSocket
 * on `/api/setup/setup-progress/{session_id}` for live logs; this POST must
 * not use the 8s `apiFetch` timeout or a Server Action, because apt/user
 * creation routinely runs for minutes.
 */
export async function POST(request: NextRequest) {
  const token = (await cookies()).get(SESSION_COOKIE)?.value;
  if (!token) {
    return new Response("Authentication required", { status: 401 });
  }

  const upstream = await fetch(`${internalApiUrl()}/api/v1/setup/auto-setup`, {
    method: "POST",
    headers: {
      accept: "application/json",
      authorization: `Bearer ${token}`,
      "content-type": request.headers.get("content-type") ?? "application/json",
    },
    body: await request.text(),
    cache: "no-store",
    signal: request.signal,
  });

  return new Response(await upstream.text(), {
    status: upstream.status,
    headers: {
      "content-type": upstream.headers.get("content-type") ?? "application/json",
    },
  });
}
