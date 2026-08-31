import "server-only";
import { cookies } from "next/headers";
import { sessionTokenFrom } from "@/modules/auth/session";
import { internalApiUrl } from "@/shared/config/internal-api";

/**
 * Cookie → Bearer proxy for the assistant workspace. Browser fetch cannot
 * attach the HttpOnly JWT, and hashed Server Actions go stale after a Docker
 * image pull.
 */
export async function proxyAssistant(request: Request): Promise<Response> {
  const token = sessionTokenFrom(await cookies());
  if (!token) {
    return Response.json({ detail: "Authentication required" }, { status: 401 });
  }

  const url = new URL(request.url);
  const action = url.searchParams.get("action");
  const target = assistantUpstream(action, url.searchParams, request.method);
  if (!target.ok) {
    return Response.json({ detail: target.error }, { status: 400 });
  }

  const body = request.method === "GET" ? undefined : (await request.text()) || "{}";
  try {
    const upstream = await fetch(`${internalApiUrl()}${target.path}`, {
      method: target.method,
      headers: {
        accept: "application/json",
        authorization: `Bearer ${token}`,
        ...(target.method === "GET" ? {} : { "content-type": "application/json" }),
      },
      body,
      cache: "no-store",
      signal: AbortSignal.timeout(20_000),
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

function assistantUpstream(
  action: string | null,
  params: URLSearchParams,
  method: string,
): { ok: true; method: "GET" | "POST"; path: string } | { ok: false; error: string } {
  const id = params.get("id")?.trim() || "";
  const runId = params.get("runId")?.trim() || "";
  const toolId = params.get("toolId")?.trim() || "";

  if (action === "workspace" && method === "GET") {
    return { ok: true, method: "GET", path: "/api/v1/assistant" };
  }
  if (action === "create" && method === "POST") {
    return { ok: true, method: "POST", path: "/api/v1/assistant/conversations" };
  }
  if (action === "conversation" && method === "GET" && id) {
    return { ok: true, method: "GET", path: `/api/v1/assistant/conversations/${id}` };
  }
  if (action === "send" && method === "POST" && id) {
    return {
      ok: true,
      method: "POST",
      path: `/api/v1/assistant/conversations/${id}/messages`,
    };
  }
  if (action === "interrupt" && method === "POST" && id) {
    return {
      ok: true,
      method: "POST",
      path: `/api/v1/assistant/conversations/${id}/interrupt`,
    };
  }
  if (action === "run" && method === "GET" && id) {
    return { ok: true, method: "GET", path: `/api/v1/assistant/runs/${id}` };
  }
  if (action === "decide" && method === "POST" && runId && toolId) {
    return {
      ok: true,
      method: "POST",
      path: `/api/v1/assistant/runs/${runId}/tools/${toolId}`,
    };
  }
  return { ok: false, error: "Unknown assistant action" };
}
