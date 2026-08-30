/**
 * FastAPI origin used only on the Next.js server. The browser never sees this
 * value: `/api/*`, `/health`, and `/static/*` are rewritten to it.
 *
 * Read at call time (dynamic key) so Docker/runtime `INTERNAL_API_URL` is not
 * inlined to the build-time default. Set it in `frontend/.env` for `next dev`,
 * or in the process environment for `next start` / the standalone image.
 */
export const DEFAULT_INTERNAL_API_URL = "http://127.0.0.1:8000";

export function internalApiUrl(): string {
  const raw = process.env["INTERNAL_API_URL"];
  if (typeof raw === "string" && raw.trim()) {
    return raw.trim().replace(/\/$/, "");
  }
  return DEFAULT_INTERNAL_API_URL;
}
