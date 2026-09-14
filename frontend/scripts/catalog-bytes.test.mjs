import assert from "node:assert/strict";
import test from "node:test";
import {
  LOGIN_NAMESPACES,
  OVERVIEW_NAMESPACES,
  catalogByteReport,
  compactBytes,
  pickNamespaces,
} from "./catalog-bytes.mjs";

test("full catalogs are larger than login and overview subsets", () => {
  const report = catalogByteReport();
  const english = report.locales["en-US"];
  const chinese = report.locales["zh-CN"];
  assert.ok(english.full > 100_000);
  assert.ok(chinese.full > 100_000);
  assert.ok(english.login_subset < english.full / 2);
  assert.ok(english.overview_subset < english.full / 2);
  assert.equal(report.client_estimate.login, english.login_subset);
  assert.equal(report.client_estimate.overview, english.overview_subset);
  assert.equal(compactBytes({ a: 1 }), Buffer.byteLength('{"a":1}', "utf8"));
  assert.deepEqual(
    pickNamespaces(
      { feedback: { ok: "OK" }, login: { submit: "Sign in" }, other: 2 },
      LOGIN_NAMESPACES,
    ),
    { feedback: { ok: "OK" }, login: { submit: "Sign in" } },
  );
  assert.ok(!OVERVIEW_NAMESPACES.includes("plugins"));
  assert.ok(OVERVIEW_NAMESPACES.includes("plugins.aiImport"));
});
