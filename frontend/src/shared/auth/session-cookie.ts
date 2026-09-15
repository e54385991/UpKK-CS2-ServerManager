import "server-only";

import { SESSION_COOKIE, sessionCookieName } from "@/shared/auth/session-cookie-name";

export { SESSION_COOKIE, sessionCookieName };

export function sessionTokenFrom(store: {
  get(name: string): { value: string } | undefined;
}): string | undefined {
  return store.get(sessionCookieName())?.value;
}

/** Cheap presence check for this instance only; layouts still validate the JWT. */
export function hasSessionCookie(store: { has(name: string): boolean }): boolean {
  return store.has(sessionCookieName());
}
