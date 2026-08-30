import { cookies } from "next/headers";
import type { NextRequest } from "next/server";
import { SESSION_COOKIE } from "@/modules/auth/session";
import { internalApiUrl } from "@/shared/config/internal-api";

/**
 * Cookie → Bearer upload proxy. Multipart uploads stay off Server Actions
 * (body-size limits) and never talk to FastAPI from the browser.
 */
export async function POST(
  request: NextRequest,
  context: { params: Promise<{ serverId: string }> },
) {
  const token = (await cookies()).get(SESSION_COOKIE)?.value;
  if (!token) {
    return new Response("Authentication required", { status: 401 });
  }

  const { serverId } = await context.params;
  const path = request.nextUrl.searchParams.get("path") ?? "";
  const relativePath = request.nextUrl.searchParams.get("relative_path") ?? "";
  const contentType = request.headers.get("content-type");
  if (!request.body || !contentType) {
    return new Response("Missing upload body", { status: 400 });
  }

  const query = new URLSearchParams({ path });
  if (relativePath) query.set("relative_path", relativePath);
  const headers: Record<string, string> = {
    authorization: `Bearer ${token}`,
    "content-type": contentType,
  };
  const contentLength = request.headers.get("content-length");
  if (contentLength) headers["content-length"] = contentLength;
  const upstream = await fetch(
    `${internalApiUrl()}/api/v1/servers/${serverId}/files/upload?${query.toString()}`,
    {
      method: "POST",
      headers,
      body: request.body,
      duplex: "half",
      cache: "no-store",
      signal: request.signal,
    } as RequestInit,
  );

  return new Response(await upstream.text(), {
    status: upstream.status,
    headers: {
      "content-type": upstream.headers.get("content-type") ?? "application/json",
    },
  });
}
