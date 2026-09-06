import assert from "node:assert/strict";
import test from "node:test";
import { safeUrl } from "./url.ts";

test("safeUrl accepts http, https, mailto, and same-origin paths", () => {
  assert.equal(safeUrl("https://github.com/a/b"), "https://github.com/a/b");
  assert.equal(safeUrl(" http://example.com "), "http://example.com/");
  assert.equal(safeUrl("mailto:ops@example.com"), "mailto:ops@example.com");
  assert.equal(safeUrl("/plugins"), "/plugins");
  assert.equal(safeUrl("#install"), "#install");
});

test("safeUrl rejects script, data, and protocol-relative destinations", () => {
  assert.equal(safeUrl("javascript:alert(1)"), null);
  assert.equal(safeUrl("JavaScript:alert(1)"), null);
  assert.equal(safeUrl("data:text/html;base64,PHNjcmlwdD4="), null);
  assert.equal(safeUrl("//evil.example.com"), null);
  assert.equal(safeUrl("file:///etc/passwd"), null);
});

test("safeUrl treats blank and missing values as absent", () => {
  assert.equal(safeUrl("   "), null);
  assert.equal(safeUrl(null), null);
  assert.equal(safeUrl(undefined), null);
  assert.equal(safeUrl("not a url"), null);
});
