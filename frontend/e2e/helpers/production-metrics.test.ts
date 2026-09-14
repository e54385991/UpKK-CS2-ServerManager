import assert from "node:assert/strict";
import test from "node:test";
import { protocolFromEnv, summarize, type ProductionRouteMetrics } from "./production-metrics.ts";

function sample(overrides: Partial<ProductionRouteMetrics> = {}): ProductionRouteMetrics {
  return {
    route: "/login",
    locale: "en-US",
    ttfb_ms: 10,
    fcp_ms: 20,
    lcp_ms: 30,
    cls: 0,
    inp_ms: 8,
    long_task_count: 0,
    long_task_total_ms: 0,
    html_bytes: 1000,
    html_gzip_bytes: 400,
    rsc_bytes: 2000,
    rsc_gzip_bytes: 800,
    js_transfer_bytes: 3000,
    critical_content_ms: 40,
    ...overrides,
  };
}

test("protocol defaults to a smoke loop", () => {
  assert.deepEqual(protocolFromEnv({}), { warmup: 1, measure: 2, rounds: 1 });
});

test("summary uses nearest-rank percentiles", () => {
  const summary = summarize([
    sample({ critical_content_ms: 10, html_bytes: 100 }),
    sample({ critical_content_ms: 20, html_bytes: 200 }),
    sample({ critical_content_ms: 30, html_bytes: 300 }),
  ]);
  assert.equal(summary.count, 3);
  assert.equal(summary.critical_content_ms.p50, 20);
  assert.equal(summary.critical_content_ms.p95, 30);
  assert.equal(summary.html_bytes, 200);
  assert.equal(summary.html_gzip_bytes, 400);
});
