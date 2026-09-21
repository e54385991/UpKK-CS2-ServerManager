import "server-only";
import { cookies } from "next/headers";
import { sessionTokenFrom } from "@/modules/auth/session";
import { passkeyUpstream } from "@/modules/profile/passkey-upstream";
import { internalApiUrl } from "@/shared/config/internal-api";

/**
 * Cookie → Bearer proxy for passkey bind/rename/delete. Browser fetch cannot
 * attach the HttpOnly JWT. Origin is forwarded so WebAuthn RP identity stays
 * the console origin, not the internal FastAPI bind host.
 */
export async function proxyPasskeys(request: Request): Promise<Response> {
  const token = sessionTokenFrom(await cookies());
  if (!token) {
    return Response.json({ detail: "Authentication required" }, { status: 401 });
  }

  const target = passkeyUpstream(request.method, new URL(request.url).searchParams);
  if (!target.ok) {
    return Response.json({ detail: target.error }, { status: 400 });
  }

  const origin = request.headers.get("origin")?.trim() ?? "";
  const rawBody = target.method === "DELETE" ? "" : await request.text();
  const hasBody = rawBody.length > 0;

  try {
    const upstream = await fetch(`${internalApiUrl()}${target.path}`, {
      method: target.method,
      headers: {
        accept: "application/json",
        authorization: `Bearer ${token}`,
        ...(origin ? { origin } : {}),
        ...(hasBody ? { "content-type": "application/json" } : {}),
      },
      body: hasBody ? rawBody : undefined,
      cache: "no-store",
      signal: AbortSignal.timeout(15_000),
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
