export const PASSKEYS_PROXY_PATH = "/passkeys";

export type PasskeyUpstream =
  | { readonly ok: true; readonly method: "POST" | "PATCH" | "DELETE"; readonly path: string }
  | { readonly ok: false; readonly error: string };

const CREDENTIAL_ID = /^\d+$/;

export function passkeyUpstream(method: string, params: URLSearchParams): PasskeyUpstream {
  const action = params.get("action");
  const id = params.get("id")?.trim() ?? "";
  if (action === "register-options" && method === "POST") {
    return { ok: true, method: "POST", path: "/api/v1/auth/passkeys/register/options" };
  }
  if (action === "register-verify" && method === "POST") {
    return { ok: true, method: "POST", path: "/api/v1/auth/passkeys/register/verify" };
  }
  if (CREDENTIAL_ID.test(id) && method === "PATCH") {
    return { ok: true, method: "PATCH", path: `/api/v1/auth/passkeys/${id}` };
  }
  if (CREDENTIAL_ID.test(id) && method === "DELETE") {
    return { ok: true, method: "DELETE", path: `/api/v1/auth/passkeys/${id}` };
  }
  return { ok: false, error: "Unknown passkey action" };
}
