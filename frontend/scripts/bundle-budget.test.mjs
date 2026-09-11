import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, mkdir, writeFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { gzipSync } from "node:zlib";
import { measureBundles, bundleViolations } from "./bundle-budget.mjs";

async function fixture(t) {
  const root = await mkdtemp(join(tmpdir(), "next-bundle-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  async function write(path, content) {
    const file = join(root, path);
    await mkdir(dirname(file), { recursive: true });
    await writeFile(file, content);
  }
  await write("static/core.js", "runtime");
  await write("static/page.js", "page-specific code");
  await write("static/lazy.js", "only loaded after interaction");
  await write("server/app/example/page/build-manifest.json", JSON.stringify({ rootMainFiles: ["static/core.js"], polyfillFiles: [] }));
  await write("server/app/example/page_client-reference-manifest.js", `globalThis.__RSC_MANIFEST = ${JSON.stringify({ "/example/page": {
    entryJSFiles: { layout: ["static/core.js"], loading: ["static/page.js"], page: ["static/core.js", "static/page.js"] },
    clientModules: { lazy: { chunks: ["static/lazy.js"] } },
  } })}`);
  return { root, write };
}

test("counts page/layout/loading entries once, excludes lazy chunks and detects overflow", async t => {
  const { root } = await fixture(t);
  const result = await measureBundles(root);
  const expected = gzipSync("runtime").length + gzipSync("page-specific code").length;
  assert.equal(result.routes[0].gzip, expected);
  assert.equal(result.chunkSizes.size, 2);
  assert.equal(bundleViolations(result, expected - 1, 1000).length, 1);
  assert.equal(bundleViolations(result, expected, 1000).length, 0);
  assert.equal(bundleViolations(result, 1000, gzipSync("runtime").length).length, 1);
});

test("shared chunks count towards each route, without repeated entries", async t => {
  const { root, write } = await fixture(t);
  await write("server/app/other/page/build-manifest.json", JSON.stringify({ rootMainFiles: ["static/core.js"], polyfillFiles: [] }));
  await write("server/app/other/page_client-reference-manifest.js", 'globalThis.__RSC_MANIFEST = {"/other/page": {entryJSFiles: {page: ["static/core.js"]}}}');
  const result = await measureBundles(root);
  assert.equal(result.routes[1].gzip, gzipSync("runtime").length);
  assert.equal(result.chunkSizes.size, 2);
});

test("missing or malformed manifests and missing entry assets fail closed", async t => {
  const { root, write } = await fixture(t);
  await rm(join(root, "static/page.js"));
  await assert.rejects(measureBundles(root), /ENOENT/);
  await write("server/app/example/page_client-reference-manifest.js", "globalThis.__RSC_MANIFEST = {}");
  await assert.rejects(measureBundles(root), /Invalid Next.js client/);
  await rm(join(root, "server/app/example/page_client-reference-manifest.js"));
  await assert.rejects(measureBundles(root), /ENOENT/);
  await rm(join(root, "server/app/example"), { recursive: true });
  await assert.rejects(measureBundles(root), /No Next.js page/);
});
