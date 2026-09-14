import assert from "node:assert/strict";
import test from "node:test";
import {
  chartIndexFromLocalX,
  chartLinePaths,
  formatChartClock,
  formatChartNumber,
  nearestChartIndex,
  nearestNumericChartIndex,
  sparkSegments,
} from "./monitor-chart-geometry.ts";

const x = (index: number) => index * 10;
const y = (value: number) => 100 - value;

test("chartLinePaths break the stroke on null samples", () => {
  const paths = chartLinePaths(
    [
      { ts: "a", value: 10 },
      { ts: "b", value: null },
      { ts: "c", value: 20 },
    ],
    x,
    y,
  );
  assert.equal(paths.length, 2);
  assert.match(paths[0]?.d ?? "", /^M0 90/);
  assert.match(paths[1]?.d ?? "", /^M20 80/);
});

test("nearestChartIndex returns -1 without a usable marker", () => {
  const series = [
    { ts: "2026-09-13T04:00:00.000Z", value: 1 },
    { ts: "2026-09-13T04:00:10.000Z", value: 2 },
  ];
  assert.equal(nearestChartIndex([], "2026-09-13T04:00:00.000Z"), -1);
  assert.equal(nearestChartIndex(series, null), -1);
  assert.equal(nearestChartIndex(series, "nope"), -1);
  assert.equal(nearestChartIndex(series, "2026-09-13T04:00:09.000Z"), 1);
});

test("sparkSegments omit empty series and split around gaps", () => {
  assert.deepEqual(sparkSegments([]), []);
  assert.deepEqual(sparkSegments([null, null]), []);
  const segments = sparkSegments([10, null, 20, 0]);
  assert.equal(segments.length, 2);
  assert.match(segments[0] ?? "", /^0,/);
  assert.match(segments[1] ?? "", /^2,/);
});

test("chart labels stay compact", () => {
  assert.equal(formatChartClock("not-a-date"), "");
  assert.equal(formatChartClock("2026-09-13T04:00:00.000Z").length > 0, true);
  assert.equal(formatChartNumber(100), "100");
  assert.equal(formatChartNumber(10.55), "10.6");
  assert.equal(formatChartNumber(1.234), "1.23");
});

test("chartIndexFromLocalX maps pointer X onto the plot", () => {
  assert.equal(chartIndexFromLocalX(0, 0, 640, 44, 12), -1);
  assert.equal(chartIndexFromLocalX(100, 1, 640, 44, 12), 0);
  assert.equal(chartIndexFromLocalX(44, 11, 640, 44, 12), 0);
  assert.equal(chartIndexFromLocalX(640 - 12, 11, 640, 44, 12), 10);
  assert.equal(chartIndexFromLocalX(44 + (640 - 44 - 12) / 2, 11, 640, 44, 12), 5);
});

test("nearestNumericChartIndex skips null samples", () => {
  const series = [
    { ts: "a", value: 1 },
    { ts: "b", value: null },
    { ts: "c", value: 3 },
  ];
  assert.equal(nearestNumericChartIndex([], 0), -1);
  assert.equal(nearestNumericChartIndex(series, 0), 0);
  assert.equal(nearestNumericChartIndex(series, 1), 0);
  assert.equal(nearestNumericChartIndex(series, 2), 2);
});
