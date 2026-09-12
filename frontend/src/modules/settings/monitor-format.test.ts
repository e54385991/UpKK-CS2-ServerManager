import assert from "node:assert/strict";
import test from "node:test";
import {
  formatAlertDetail,
  formatAlertTitle,
  formatCpu,
  formatMonitorInstant,
  formatRpm,
  isLocalizedAlert,
  monitorExportFilename,
  overallTone,
  sparkValues,
} from "./monitor-format.ts";
import type { PanelMonitorView } from "./monitor-types.ts";

function view(overrides: Partial<PanelMonitorView> = {}): PanelMonitorView {
  return {
    range: "1h",
    instance_id: "1-fixture",
    instances: [],
    status: {
      enabled: true,
      running: true,
      stale: false,
      history_available: true,
      history_error: null,
      config_sync_error: null,
      last_sample_at: "2026-09-13T04:00:00.000Z",
      stopped_at: null,
      instance_id: "1-fixture",
      sample_interval_seconds: 10,
      dropped_error_details: 0,
      truncated_summaries: 0,
      collection_ms: 4,
      collecting: true,
    },
    snapshot: null,
    series: [{
      ts: "2026-09-13T04:00:00.000Z",
      request_count: 20,
      requests_per_minute: 40,
      status_5xx: 0,
      error_rate: 0,
      latency_p50_ms: 10,
      latency_p95_ms: 20,
      latency_p99_ms: 30,
      latency_max_ms: 40,
      latency_partial: false,
      cpu_percent: 4,
      rss_bytes: 100,
      rss_peak_bytes: 120,
      loop_lag_p95_ms: 2,
      db_checked_out: 1,
      db_capacity: 15,
      redis_ping_ms: 1,
      redis_connected: true,
      queue_running: 0,
      queue_queued: 0,
      oldest_queue_ms: null,
      failed_tasks: 0,
      unhandled_exceptions: 0,
      fd_open: 8,
      fd_limit: 1024,
    }],
    alerts: [],
    error_groups: [],
    integrity: {
      sample_interval_seconds: 10,
      display_bucket_seconds: 30,
      point_count: 1,
      gap: false,
      partial_latency: false,
    },
    ...overrides,
  };
}

test("disabled monitoring is never reported as healthy", () => {
  assert.equal(
    overallTone(view({ status: { ...view().status, enabled: false }, alerts: [{
      id: "x",
      severity: "critical",
      metric: "redis",
      title: "down",
      detail: "down",
      guidance: "check",
      threshold: "fail",
      value: null,
      since: null,
    }] })),
    "neutral",
  );
});

test("critical alerts beat watch-level data issues", () => {
  const base = view();
  assert.equal(
    overallTone(view({
      alerts: [{
        id: "p95",
        severity: "critical",
        metric: "request_p95",
        title: "slow",
        detail: "slow",
        guidance: "inspect",
        threshold: ">=1000",
        value: 1200,
        since: null,
      }],
      status: { ...base.status, stale: true },
    })),
    "danger",
  );
});

test("empty series and missing history are not healthy", () => {
  assert.equal(overallTone(view({ series: [] })), "neutral");
  assert.equal(
    overallTone(view({ status: { ...view().status, history_available: false } })),
    "warn",
  );
});

test("formatters stay compact and export names include the range", () => {
  assert.equal(formatCpu(null), "—");
  assert.equal(formatCpu(6.2), "6.2%");
  assert.equal(formatRpm(48.2), "48");
  assert.equal(isLocalizedAlert("redis"), true);
  assert.equal(isLocalizedAlert("unknown"), false);
  assert.equal(
    monitorExportFilename("24h", new Date("2026-09-13T04:00:00.000Z")),
    "cs2-panel-monitor-24h-2026-09-13T04-00-00.json",
  );
  assert.deepEqual(
    sparkValues([{ ts: "a", value: 1 }, { ts: "b", value: null }], (point) => (point as { value: number | null }).value),
    [1, null],
  );
});

test("sample times combine relative language with a local clock", () => {
  const now = Date.parse("2026-09-13T04:00:12.000Z");
  const english = formatMonitorInstant("2026-09-13T04:00:00.000Z", "en-US", now);
  const chinese = formatMonitorInstant("2026-09-13T04:00:00.000Z", "zh-CN", now);
  assert.match(english, /12 seconds ago/);
  assert.match(english, /\(/);
  assert.match(chinese, /秒/);
  assert.equal(formatMonitorInstant(null, "en-US", now), "—");
});

test("localized alert details pick watch vs critical copy", () => {
  const t = (key: string) => key;
  const base = {
    title: "fallback-title",
    detail: "fallback-detail",
    guidance: "fallback-guidance",
    value: 1200,
  };
  assert.equal(
    formatAlertTitle(t, { ...base, id: "request_p95", severity: "critical" }),
    "alerts.request_p95.titleCritical",
  );
  assert.equal(
    formatAlertDetail(t, { ...base, id: "request_p95", severity: "critical" }),
    "alerts.request_p95.detailCritical",
  );
  assert.equal(
    formatAlertDetail(t, { ...base, id: "http_5xx", severity: "watch", value: 6 }),
    "alerts.http_5xx.detail",
  );
  assert.equal(
    formatAlertDetail(t, { ...base, id: "http_5xx", severity: "critical", value: 0.15 }),
    "alerts.http_5xx.detailCritical",
  );
  assert.equal(
    formatAlertDetail(t, { ...base, id: "unknown", severity: "critical" }),
    "fallback-detail",
  );
});
