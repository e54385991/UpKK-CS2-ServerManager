import { test } from "node:test";
import assert from "node:assert/strict";
import { excludesLocaleRewrite, internalLocalePaths, isConsolePath, loginReturnHref, normalizePublicPath } from "./paths.ts";

test("internal and encoded locale prefixes cannot bypass the public auth guard", () => {
  for (const input of ["/en-US/settings", "/zh-CN/settings", "/%65n-US/%73ettings", "/%7Ah-CN/servers/1"]) {
    const { pathname, prefixed } = normalizePublicPath(input);
    assert.equal(prefixed, true);
    assert.equal(isConsolePath(pathname), true);
  }
  assert.equal(isConsolePath(normalizePublicPath("/servers-public").pathname), false);
});
test("public invalidation reaches both internal languages, including the root", () => {
  assert.deepEqual(internalLocalePaths("/servers/123/files"), ["/en-US/servers/123/files", "/zh-CN/servers/123/files"]);
  assert.deepEqual(internalLocalePaths("/"), ["/en-US", "/zh-CN"]);
});
test("JSON, SSE, upload, static and API routes stay outside language rewriting", () => {
  for (const path of ["/api/v1/auth/me", "/health", "/static/plugins.zip", "/files-upload/servers/1", "/ops-stream/operations/inbox", "/ai-stream/runs/1", "/passkeys", "/server-ops/inbox", "/_next/static/chunk.js"]) {
    assert.equal(excludesLocaleRewrite(path), true, path);
  }
});
test("login return addresses preserve public queries and reject external paths", () => {
  assert.equal(loginReturnHref("/servers/1/files?path=%2Fsrv"), "/login?next=%2Fservers%2F1%2Ffiles%3Fpath%3D%252Fsrv");
  assert.equal(loginReturnHref("//attacker.invalid"), "/login");
});
