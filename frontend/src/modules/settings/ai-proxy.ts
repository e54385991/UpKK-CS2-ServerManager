import "server-only";
import { cookies } from "next/headers";
import { SESSION_COOKIE } from "@/modules/auth/session";
import { internalApiUrl } from "@/shared/config/internal-api";

const UPSTREAM = {
  profile: "/api/v1/profile/ai",
  system: "/api/v1/settings/ai",
} as const;

/**
 * Cookie → Bearer proxy for AI settings. Browser fetch cannot attach the
 * HttpOnly JWT, and hashed Server Actions go stale after a Docker image pull.
 */
export async function proxyAiSettings(
  request: Request,
  method: "GET" | "PUT" | "POST",
): Promise<Response> {
  const token = (await cookies()).get(SESSION_COOKIE)?.value;
  if (!token) {
    return Response.json({ detail: "Authentication required" }, { status: 401 });
  }

  const scope = new URL(request.url).searchParams.get("scope") === "profile"
    ? "profile"
    : "system";
  const path = method === "POST" ? `${UPSTREAM[scope]}/test` : UPSTREAM[scope];
  const body = method === "GET" ? undefined : (await request.text()) || "{}";

  try {
    const upstream = await fetch(`${internalApiUrl()}${path}`, {
      method,
      headers: {
        accept: "application/json",
        authorization: `Bearer ${token}`,
        ...(method === "GET" ? {} : { "content-type": "application/json" }),
      },
      body,
      cache: "no-store",
      signal: AbortSignal.timeout(method === "POST" ? 180_000 : 15_000),
    });
    return new Response(await upstream.text(), {
      status: upstream.status,
      headers: {
        "content-type": upstream.headers.get("content-type") ?? "application/json",
      },
    });
  } catch (error) {
    const detail = error instanceof Error ? error.message : "upstream unavailable";
    return Response.json({ detail }, { status: 502 });
  }
}
