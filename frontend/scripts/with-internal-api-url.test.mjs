import assert from "node:assert/strict";
import test from "node:test";
import { rewriteApiDestinations } from "./with-internal-api-url.mjs";

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
