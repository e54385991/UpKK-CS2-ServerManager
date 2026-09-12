import assert from "node:assert/strict";
import test from "node:test";
import { collectBrowserMetrics } from "./browser-metrics.ts";
import {
  buildPerformanceExport,
  formatBytes,
  formatMs,
  formatPercent,
  performanceExportFilename,
} from "./diagnostics-format.ts";
import type { PanelPerformanceSnapshotDto } from "@/shared/api/types";

test("browser metrics stay empty when Performance is unavailable", () => {
  const metrics = collectBrowserMetrics(new Date("2026-09-13T04:00:00.000Z"), null);
  assert.equal(metrics.captured_at, "2026-09-13T04:00:00.000Z");
  assert.equal(metrics.navigation.ttfb_ms, null);
  assert.equal(metrics.resource_count, 0);
});

test("browser metrics read navigation, paint, and layout-shift entries", () => {
  const performanceLike = {
    getEntriesByType(type: string) {
      if (type === "navigation") {
        return [
          {
            requestStart: 10,
            responseStart: 40,
            domContentLoadedEventEnd: 120,
            loadEventEnd: 200,
            startTime: 0,
            transferSize: 4096,
          },
        ];
      }
      if (type === "paint") {
        return [{ name: "first-contentful-paint", startTime: 80.44 }];
      }
      if (type === "layout-shift") {
        return [
          { value: 0.1, hadRecentInput: false },
          { value: 0.5, hadRecentInput: true },
        ];
      }
      if (type === "resource") {
        return [{ transferSize: 100 }, { transferSize: 50 }];
      }
      return [];
    },
  };
  const metrics = collectBrowserMetrics(
    new Date("2026-09-13T04:00:00.000Z"),
    performanceLike as Pick<Performance, "getEntriesByType">,
  );
  assert.equal(metrics.navigation.ttfb_ms, 30);
  assert.equal(metrics.navigation.dcl_ms, 120);
  assert.equal(metrics.fcp_ms, 80.4);
  assert.equal(metrics.cls, 0.1);
  assert.equal(metrics.resource_transfer_bytes, 150);
});

test("export JSON keeps the backend snapshot and appends browser last", () => {
  const snapshot = {
    format: "upkk-panel-performance",
    version: 1,
    captured_at: "2026-09-13T04:00:00.000Z",
    priority: ["requests", "process"],
  } as PanelPerformanceSnapshotDto;
  const browser = collectBrowserMetrics(new Date("2026-09-13T04:00:01.000Z"), null);
  const exported = buildPerformanceExport(snapshot, browser);
  assert.deepEqual(exported.priority, ["requests", "process", "browser"]);
  assert.equal(exported.browser?.resource_count, 0);
  assert.equal(
    performanceExportFilename(snapshot.captured_at),
    "cs2-panel-performance-2026-09-13T04-00-00.json",
  );
});

test("byte and latency formatters stay compact for the dashboard", () => {
  assert.equal(formatBytes(null), "—");
  assert.equal(formatBytes(512), "512 B");
  assert.equal(formatBytes(2048), "2.0 KiB");
  assert.equal(formatMs(12.34), "12.3 ms");
  assert.equal(formatPercent(0.0123), "1.23%");
});
