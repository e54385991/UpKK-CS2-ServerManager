/** Public callback Google redirects to. Next rewrites `/api/*` to FastAPI. */
export const GMAIL_OAUTH_CALLBACK_PATH = "/api/gmail-oauth/callback";

export function gmailRedirectUri(origin: string): string {
  return `${origin.trim().replace(/\/+$/, "")}${GMAIL_OAUTH_CALLBACK_PATH}`;
}
