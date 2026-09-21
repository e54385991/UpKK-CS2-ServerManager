export const GOOGLE_ID_TOKEN_MESSAGE = "google-oauth-token";

export function googleIdTokenFromMessage(data: unknown): string | null {
  if (!data || typeof data !== "object") return null;
  const record = data as { type?: unknown; id_token?: unknown };
  if (
    record.type !== GOOGLE_ID_TOKEN_MESSAGE ||
    typeof record.id_token !== "string" ||
    !record.id_token
  ) {
    return null;
  }
  return record.id_token;
}

/** Opens the same Google ID-token popup the login page uses. */
export function openGoogleIdTokenPopup(clientId: string): Window | null {
  const authUrl = new URL("https://accounts.google.com/o/oauth2/v2/auth");
  authUrl.searchParams.set("client_id", clientId);
  authUrl.searchParams.set("redirect_uri", `${window.location.origin}/google-callback`);
  authUrl.searchParams.set("response_type", "id_token");
  authUrl.searchParams.set("scope", "openid email profile");
  authUrl.searchParams.set("nonce", String(Date.now()));
  const width = 500;
  const height = 600;
  const left = Math.max(0, Math.round(window.screen.width / 2 - width / 2));
  const top = Math.max(0, Math.round(window.screen.height / 2 - height / 2));
  return window.open(
    authUrl.toString(),
    "Google Sign-In",
    `width=${width},height=${height},left=${left},top=${top},resizable=yes,scrollbars=yes`,
  );
}
