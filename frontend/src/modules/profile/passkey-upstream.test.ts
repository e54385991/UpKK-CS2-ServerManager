import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { PASSKEYS_PROXY_PATH, passkeyUpstream } from "./passkey-upstream.ts";

test("passkeyUpstream maps bind rename and delete onto versioned FastAPI paths", () => {
  assert.deepEqual(passkeyUpstream("POST", new URLSearchParams("action=register-options")), {
    ok: true,
    method: "POST",
    path: "/api/v1/auth/passkeys/register/options",
  });
  assert.deepEqual(passkeyUpstream("POST", new URLSearchParams("action=register-verify")), {
    ok: true,
    method: "POST",
    path: "/api/v1/auth/passkeys/register/verify",
  });
  assert.deepEqual(passkeyUpstream("PATCH", new URLSearchParams("id=11")), {
    ok: true,
    method: "PATCH",
    path: "/api/v1/auth/passkeys/11",
  });
  assert.deepEqual(passkeyUpstream("DELETE", new URLSearchParams("id=11")), {
    ok: true,
    method: "DELETE",
    path: "/api/v1/auth/passkeys/11",
  });
});

test("passkeyUpstream rejects unknown or unsafe targets", () => {
  assert.equal(passkeyUpstream("POST", new URLSearchParams()).ok, false);
  assert.equal(passkeyUpstream("GET", new URLSearchParams("action=register-options")).ok, false);
  assert.equal(passkeyUpstream("PATCH", new URLSearchParams("id=11/../users")).ok, false);
  assert.equal(passkeyUpstream("DELETE", new URLSearchParams("id=abc")).ok, false);
});

test("the profile form talks to the cookie-to-bearer proxy instead of FastAPI", () => {
  const source = readFileSync(new URL("./passkey-form.tsx", import.meta.url), "utf8");
  assert.match(source, /PASSKEYS_PROXY_PATH/);
  assert.doesNotMatch(source, /\/api\/v1\/auth\/passkeys/);
  assert.equal(PASSKEYS_PROXY_PATH, "/passkeys");
});
