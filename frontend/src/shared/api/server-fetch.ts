import "server-only";
import { cookies, headers } from "next/headers";
import { sessionTokenFrom } from "@/modules/auth/session";
import { internalApiUrl } from "@/shared/config/internal-api";

export type ApiResult<T> =
  | { readonly ok: true; readonly data: T }
  | { readonly ok: false; readonly status: number; readonly error: string };

export type ApiFetchInit = Omit<RequestInit, "signal"> & {
  signal?: AbortSignal | null;
  timeoutMs?: number;
};

// Headers a reverse proxy (or Next itself) puts the visitor address in. This
// call is a new connection from the Next server, so without forwarding them
// the backend would attribute every console action to the Next container.
// Which one the backend trusts is an admin setting, so pass through whichever
// the incoming request actually carries.
const CLIENT_ADDRESS_HEADERS = [
  "x-forwarded-for",
  "x-real-ip",
  "cf-connecting-ip",
  "true-client-ip",
  "fastly-client-ip",
  "forwarded",
] as const;

async function clientAddressHeaders(): Promise<Record<string, string>> {
  const incoming = await headers();
  const forwarded: Record<string, string> = {};
  for (const name of CLIENT_ADDRESS_HEADERS) {
    const value = incoming.get(name);
    if (value) forwarded[name] = value;
  }
  return forwarded;
}

/**
 * Server-side call to the internal FastAPI. Attaches the session cookie's JWT
 * as a bearer token, forwards the visitor address, and never caches. Auth and
 * transport failures are returned as structured results instead of thrown so
 * pages can render a degraded state.
 *
 * Default timeout is 8s for ordinary reads. Long SSH/setup calls must pass
 * `timeoutMs` (or their own `signal`) so they are not aborted mid-flight.
 */
export async function apiFetch<T>(
  path: string,
  init?: ApiFetchInit,
): Promise<ApiResult<T>> {
  const token = sessionTokenFrom(await cookies());
  const clientAddress = await clientAddressHeaders();
  const { timeoutMs, signal, headers: extraHeaders, ...rest } = init ?? {};
  try {
    const response = await fetch(`${internalApiUrl()}${path}`, {
      ...rest,
      cache: "no-store",
      signal: signal ?? AbortSignal.timeout(timeoutMs ?? 8000),
      headers: {
        accept: "application/json",
        ...clientAddress,
        ...(token ? { authorization: `Bearer ${token}` } : {}),
        ...extraHeaders,
      },
    });
    if (!response.ok) {
      return {
        ok: false,
        status: response.status,
        error: await readApiError(response, path),
      };
    }
    const data = (await response.json()) as T;
    return { ok: true, data };
  } catch (error) {
    return {
      ok: false,
      status: 0,
      error: error instanceof Error ? error.message : "network error",
    };
  }
}

async function readApiError(response: Response, path: string): Promise<string> {
  const fallback = `Request to ${path} failed with ${response.status}`;
  try {
    const body = (await response.json()) as { detail?: unknown };
    if (typeof body.detail === "string" && body.detail.trim()) {
      return body.detail;
    }
    if (Array.isArray(body.detail) && body.detail.length > 0) {
      const first = body.detail[0] as { msg?: unknown };
      if (typeof first?.msg === "string" && first.msg.trim()) {
        return first.msg;
      }
    }
    if (body.detail && typeof body.detail === "object") {
      const detail = body.detail as { message?: unknown };
      if (typeof detail.message === "string" && detail.message.trim()) {
        return detail.message;
      }
    }
  } catch {
    // The error body is optional; keep the status-based fallback.
  }
  return fallback;
}
