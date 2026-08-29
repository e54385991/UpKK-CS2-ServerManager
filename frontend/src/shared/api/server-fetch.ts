import "server-only";
import { cookies } from "next/headers";
import { SESSION_COOKIE } from "@/modules/auth/session";

const INTERNAL_API_URL =
  process.env.INTERNAL_API_URL?.replace(/\/$/, "") ?? "http://127.0.0.1:8000";

export type ApiResult<T> =
  | { readonly ok: true; readonly data: T }
  | { readonly ok: false; readonly status: number; readonly error: string };

/**
 * Server-side call to the internal FastAPI. Attaches the session cookie's JWT
 * as a bearer token and never caches. Auth and transport failures are returned
 * as structured results instead of thrown so pages can render a degraded state.
 */
export async function apiFetch<T>(
  path: string,
  init?: RequestInit,
): Promise<ApiResult<T>> {
  const token = (await cookies()).get(SESSION_COOKIE)?.value;
  try {
    const response = await fetch(`${INTERNAL_API_URL}${path}`, {
      ...init,
      cache: "no-store",
      headers: {
        accept: "application/json",
        ...(token ? { authorization: `Bearer ${token}` } : {}),
        ...init?.headers,
      },
    });
    if (!response.ok) {
      return {
        ok: false,
        status: response.status,
        error: `Request to ${path} failed with ${response.status}`,
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
