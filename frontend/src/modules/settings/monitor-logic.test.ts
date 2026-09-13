import assert from "node:assert/strict";
import test from "node:test";
import {
  discardedErrorCount,
  focusedSeries,
  kpiTone,
  matchesErrorFilter,
  monitorStatusKey,
  uniqueNonEmpty,
} from "./monitor-logic.ts";

const enabled = {
  enabled: true,
  stale: false,
  history_available: true,
};

test("uniqueNonEmpty drops blanks and keeps first-seen order", () => {
  assert.deepEqual(uniqueNonEmpty(["request", "", null, "request", "task", undefined]), [
    "request",
    "task",
  ]);
});

test("error filters treat all as a pass-through", () => {
  const item = { source: "request", severity: "error" };
  assert.equal(matchesErrorFilter(item, "all", "all"), true);
  assert.equal(matchesErrorFilter(item, "request", "error"), true);
  assert.equal(matchesErrorFilter(item, "task", "all"), false);
  assert.equal(matchesErrorFilter(item, "all", "critical"), false);
});

test("focusedSeries keeps a 15-minute window around a valid timestamp", () => {
  const series = [
    { ts: "2026-09-13T03:40:00.000Z", value: 1 },
    { ts: "2026-09-13T04:00:00.000Z", value: 2 },
    { ts: "2026-09-13T04:20:00.000Z", value: 3 },
  ];
  assert.deepEqual(focusedSeries(series, null).map((point) => point.value), [1, 2, 3]);
  assert.deepEqual(focusedSeries(series, "not-a-date").map((point) => point.value), [1, 2, 3]);
  assert.deepEqual(
    focusedSeries(series, "2026-09-13T04:00:00.000Z").map((point) => point.value),
    [2],
  );
});

test("monitorStatusKey prefers loading, disabled, then data-quality states", () => {
  assert.equal(monitorStatusKey(null, "ok"), "loading");
  assert.equal(monitorStatusKey({ status: { ...enabled, enabled: false } }, "danger"), "disabled");
  assert.equal(monitorStatusKey({ status: { ...enabled, stale: true } }, "ok"), "stale");
  assert.equal(
    monitorStatusKey({ status: { ...enabled, history_available: false } }, "ok"),
    "historyUnavailable",
  );
  assert.equal(monitorStatusKey({ status: enabled, series: [{}] }, "danger"), "critical");
  assert.equal(monitorStatusKey({ status: enabled, series: [{}] }, "warn"), "watch");
  assert.equal(monitorStatusKey({ status: enabled, series: [] }, "ok"), "collecting");
  assert.equal(monitorStatusKey({ status: enabled, series: [{}] }, "ok"), "healthy");
});

test("kpiTone and discarded counts stay conservative", () => {
  assert.equal(kpiTone(null, 500, 1000), "ok");
  assert.equal(kpiTone(500, 500, 1000), "warn");
  assert.equal(kpiTone(1000, 500, 1000), "danger");
  assert.equal(discardedErrorCount(undefined, 0), 0);
  assert.equal(discardedErrorCount(2, 4), 4);
  assert.equal(discardedErrorCount(5, 1), 5);
});
