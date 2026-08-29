import { cookies } from "next/headers";
import type { NextRequest } from "next/server";
import { SESSION_COOKIE } from "@/modules/auth/session";
import { internalApiUrl } from "@/shared/config/internal-api";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";
export const maxDuration = 180;

const UPSTREAM: Record<"profile" | "system", string> = {
  profile: "/api/v1/profile/ai/test",
  system: "/api/v1/settings/ai/test",
};

/**
 * Cookie → Bearer proxy for the long AI provider probe.
 * The settings Test button must not use a hashed Server Action: after a
 * Docker image pull the browser keeps the previous action id and Next
 * returns "Failed to find Server Action" with no UI result.
 */
export async function POST(request: NextRequest) {
  const token = (await cookies()).get(SESSION_COOKIE)?.value;
  if (!token) {
    return Response.json({ detail: "Authentication required" }, { status: 401 });
  }

  const scope = request.nextUrl.searchParams.get("scope") === "profile" ? "profile" : "system";
  const body = (await request.text()) || "{}";
  const upstream = await fetch(`${internalApiUrl()}${UPSTREAM[scope]}`, {
    method: "POST",
    headers: {
      accept: "application/json",
      authorization: `Bearer ${token}`,
      "content-type": "application/json",
    },
    body,
    cache: "no-store",
    signal: AbortSignal.timeout(180_000),
  });

  return new Response(await upstream.text(), {
    status: upstream.status,
    headers: {
      "content-type": upstream.headers.get("content-type") ?? "application/json",
    },
  });
}
