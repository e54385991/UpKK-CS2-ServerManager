import assert from "node:assert/strict";
import test from "node:test";
import { googleIdTokenFromMessage } from "./google-popup.ts";

test("google popup messages only accept a same-type id token", () => {
  assert.equal(googleIdTokenFromMessage(null), null);
  assert.equal(googleIdTokenFromMessage({ type: "other", id_token: "tok" }), null);
  assert.equal(
    googleIdTokenFromMessage({ type: "google-oauth-token", id_token: "" }),
    null,
  );
  assert.equal(
    googleIdTokenFromMessage({ type: "google-oauth-token", id_token: "tok" }),
    "tok",
  );
});
