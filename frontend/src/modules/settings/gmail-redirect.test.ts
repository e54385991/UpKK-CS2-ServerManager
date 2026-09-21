import assert from "node:assert/strict";
import test from "node:test";
import { gmailRedirectUri } from "./gmail-redirect.ts";

test("gmail redirect uri is the browser origin plus the proxied callback", () => {
  assert.equal(
    gmailRedirectUri("https://panel.example:31800"),
    "https://panel.example:31800/api/gmail-oauth/callback",
  );
  assert.equal(
    gmailRedirectUri("https://panel.example/"),
    "https://panel.example/api/gmail-oauth/callback",
  );
});
