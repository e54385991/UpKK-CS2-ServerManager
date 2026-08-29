import "server-only";
import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import type { SessionUserDto } from "@/shared/api/types";
import { internalApiUrl } from "@/shared/config/internal-api";

/** Backend HttpOnly session cookie. Its value is the JWT access token. */
export const SESSION_COOKIE = "upkk_access_token";

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
  const token = (await cookies()).get(SESSION_COOKIE)?.value;
  if (!token) return null;

  try {
    const response = await fetch(`${internalApiUrl()}/api/v1/auth/me`, {
      headers: { authorization: `Bearer ${token}` },
      cache: "no-store",
      signal: AbortSignal.timeout(2500),
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
