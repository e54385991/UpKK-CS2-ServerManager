/** Default backend HttpOnly session cookie. A port suffix isolates two consoles. */
export const SESSION_COOKIE = "upkk_access_token";

export function sessionCookieName(
  suffix: string | undefined = process.env["SESSION_COOKIE_SUFFIX"],
): string {
  const trimmed = suffix?.trim();
  return trimmed ? `${SESSION_COOKIE}_${trimmed}` : SESSION_COOKIE;
}
