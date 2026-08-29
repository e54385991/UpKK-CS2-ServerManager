import type { ActionResultDto } from "@/shared/api/types";

type DeleteResult =
  | { readonly ok: true; readonly data: ActionResultDto }
  | { readonly ok: false; readonly status: number; readonly error: string };

export async function deleteServerRecord(
  serverId: number,
): Promise<DeleteResult> {
  try {
    const response = await fetch(`/server-record/servers/${serverId}`, {
      method: "DELETE",
      credentials: "same-origin",
      cache: "no-store",
      headers: { accept: "application/json" },
      signal: AbortSignal.timeout(20_000),
    });
    if (!response.ok) {
      return {
        ok: false,
        status: response.status,
        error: await readBrowserApiError(response),
      };
    }
    const data = (await response.json()) as ActionResultDto;
    if (data.success === false) {
      return {
        ok: false,
        status: response.status,
        error: data.message || "delete failed",
      };
    }
    return { ok: true, data };
  } catch (error) {
    return {
      ok: false,
      status: 0,
      error: error instanceof Error ? error.message : "network error",
    };
  }
}

async function readBrowserApiError(response: Response): Promise<string> {
  const fallback = `Request failed with ${response.status}`;
  try {
    const body = (await response.json()) as { detail?: unknown; message?: unknown };
    if (typeof body.detail === "string" && body.detail.trim()) {
      return body.detail;
    }
    if (Array.isArray(body.detail) && body.detail.length > 0) {
      const first = body.detail[0] as { msg?: unknown };
      if (typeof first?.msg === "string" && first.msg.trim()) {
        return first.msg;
      }
    }
    if (typeof body.message === "string" && body.message.trim()) {
      return body.message;
    }
  } catch {
    // Keep the status fallback when the body is not JSON.
  }
  return fallback;
}
