import "server-only";
import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import type { SessionUserDto } from "@/shared/api/types";
import { internalApiUrl } from "@/shared/config/internal-api";

/** Default backend HttpOnly session cookie. A port suffix isolates two consoles on one host. */
export const SESSION_COOKIE = "upkk_access_token";

/**
 * Cookie name for this panel instance. Read at call time so Docker
 * `SESSION_COOKIE_SUFFIX` (public console port) is not inlined at build.
 */
export function sessionCookieName(): string {
  const suffix = process.env["SESSION_COOKIE_SUFFIX"]?.trim();
  return suffix ? `${SESSION_COOKIE}_${suffix}` : SESSION_COOKIE;
}

export function sessionTokenFrom(store: {
  get(name: string): { value: string } | undefined;
}): string | undefined {
  return store.get(sessionCookieName())?.value;
}

/** Cheap presence check for this instance only; layouts still validate the JWT. */
export function hasSessionCookie(store: { has(name: string): boolean }): boolean {
  return store.has(sessionCookieName());
}

export type SessionUser = {
  readonly id: number;
  readonly username: string;
  readonly email: string | null;
  readonly isAdmin: boolean;
  readonly isActive: boolean;
};

/**
 * Resolve the current user from the session cookie by asking the backend's
 * versioned `/api/v1/auth/me`. Returns `null` when there is no valid session.
 * Never throws for auth failures; transport errors also degrade to `null` so
 * the caller can redirect to login.
 */
export async function getSession(): Promise<SessionUser | null> {
  const token = sessionTokenFrom(await cookies());
  if (!token) return null;

  try {
    const response = await fetch(`${internalApiUrl()}/api/v1/auth/me`, {
      headers: { authorization: `Bearer ${token}` },
      cache: "no-store",
      signal: AbortSignal.timeout(8000),
    });
    if (!response.ok) return null;
    const data = (await response.json()) as SessionUserDto;
    return {
      id: data.id,
      username: data.username,
      email: data.email ?? null,
      isAdmin: Boolean(data.is_admin),
      isActive: Boolean(data.is_active),
    };
  } catch {
    return null;
  }
}

/** Require an authenticated session or redirect to the login page. */
export async function requireSession(): Promise<SessionUser> {
  const session = await getSession();
  if (!session) redirect("/login");
  return session;
}
