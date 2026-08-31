import assert from "node:assert/strict";
import test from "node:test";
import { existsSync, mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  apiOriginCandidates,
  hostGatewayFromRouteTable,
  pickReachableApiOrigin,
  prepareStandaloneAssets,
  resolveStandaloneServer,
  rewriteApiDestinations,
} from "./with-internal-api-url.mjs";

test("rewrites baked FastAPI destinations to the runtime origin", () => {
  const manifest = {
    rewrites: {
      beforeFiles: [
        { source: "/api/:path*", destination: "http://127.0.0.1:8000/api/:path*" },
        { source: "/health", destination: "http://127.0.0.1:8000/health" },
        { source: "/static/:path*", destination: "http://127.0.0.1:8000/static/:path*" },
        { source: "/cdn/:path*", destination: "https://cdn.example/cdn/:path*" },
      ],
      afterFiles: [],
      fallback: [],
    },
  };

  assert.equal(rewriteApiDestinations(manifest, "http://app:8000"), true);
  assert.equal(
    manifest.rewrites.beforeFiles[0].destination,
    "http://app:8000/api/:path*",
  );
  assert.equal(manifest.rewrites.beforeFiles[1].destination, "http://app:8000/health");
  assert.equal(
    manifest.rewrites.beforeFiles[2].destination,
    "http://app:8000/static/:path*",
  );
  assert.equal(
    manifest.rewrites.beforeFiles[3].destination,
    "https://cdn.example/cdn/:path*",
  );
});

test("is a no-op when destinations already match", () => {
  const manifest = {
    rewrites: {
      beforeFiles: [
        { source: "/api/:path*", destination: "http://app:8000/api/:path*" },
      ],
    },
  };
  assert.equal(rewriteApiDestinations(manifest, "http://app:8000"), false);
});

test("parses the default gateway from /proc/net/route", () => {
  const table = [
    "Iface\tDestination\tGateway\tFlags\tRefCnt\tUse\tMetric\tMask\tMTU\tWindow\tIRTT",
    "eth0\t00000000\t010012AC\t0003\t0\t0\t0\t00000000\t0\t0\t0",
  ].join("\n");
  assert.equal(hostGatewayFromRouteTable(table), "172.18.0.1");
});

test("offers docker-host fallbacks when a LAN IP is configured", () => {
  assert.deepEqual(apiOriginCandidates("http://192.168.50.245:8000", "172.18.0.1"), [
    "http://172.18.0.1:8000",
    "http://host.docker.internal:8000",
    "http://192.168.50.245:8000",
  ]);
});

test("keeps a docker DNS name pinned so a second instance cannot steal /health", () => {
  assert.deepEqual(apiOriginCandidates("http://app:8000", "172.18.0.1"), [
    "http://app:8000",
  ]);
  assert.deepEqual(
    apiOriginCandidates("http://cs2-server-manager-b:8000", "172.18.0.1"),
    ["http://cs2-server-manager-b:8000"],
  );
});

test("prefers the docker host over a healthy hairpin LAN IP", async () => {
  const origin = await pickReachableApiOrigin(
    "http://192.168.50.245:8000",
    async () => ({ ok: true }),
    "172.18.0.1",
  );
  assert.equal(origin, "http://172.18.0.1:8000");
});

test("picks the first origin that answers /health", async () => {
  const fetchImpl = async (url) => {
    if (String(url).startsWith("http://172.18.0.1:8000/")) {
      return { ok: true };
    }
    throw new Error("blocked");
  };
  const origin = await pickReachableApiOrigin(
    "http://192.168.50.245:8000",
    fetchImpl,
    "172.18.0.1",
  );
  assert.equal(origin, "http://172.18.0.1:8000");
});

test("prefers the local standalone server over next start", () => {
  const root = mkdtempSync(join(tmpdir(), "upkk-standalone-"));
  const standalone = join(root, ".next", "standalone", "server.js");
  writeFileSync(join(root, "package.json"), "{}");
  mkdirp(join(root, ".next", "standalone"));
  writeFileSync(standalone, "console.log('ok')");
  assert.equal(resolveStandaloneServer(root), standalone);
});

test("skips copying when the image already is the standalone tree", () => {
  const root = mkdtempSync(join(tmpdir(), "upkk-image-"));
  mkdirp(join(root, ".next", "static", "chunks"));
  mkdirp(join(root, "public"));
  writeFileSync(join(root, ".next", "static", "chunks", "app.js"), "ok");
  writeFileSync(join(root, "public", "favicon.ico"), "");
  writeFileSync(join(root, "server.js"), "");
  assert.doesNotThrow(() => prepareStandaloneAssets(root, join(root, "server.js")));
});

test("copies static assets into the standalone tree when missing", () => {
  const root = mkdtempSync(join(tmpdir(), "upkk-assets-"));
  mkdirp(join(root, ".next", "static", "chunks"));
  mkdirp(join(root, ".next", "standalone"));
  writeFileSync(join(root, ".next", "static", "chunks", "app.js"), "ok");
  writeFileSync(join(root, ".next", "standalone", "server.js"), "");
  prepareStandaloneAssets(root, join(root, ".next", "standalone", "server.js"));
  assert.equal(
    existsSync(join(root, ".next", "standalone", ".next", "static", "chunks", "app.js")),
    true,
  );
});

function mkdirp(dir) {
  mkdirSync(dir, { recursive: true });
}
