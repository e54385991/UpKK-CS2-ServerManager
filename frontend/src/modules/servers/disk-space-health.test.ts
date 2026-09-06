import assert from "node:assert/strict";
import test from "node:test";
import { diskHealth, diskHealthTone } from "./disk-space-health.ts";

function snapshot(overrides: Record<string, unknown> = {}) {
  return {
    serverId: 1,
    cached: true,
    usedGb: 30,
    totalGb: 100,
    availableGb: 60,
    usedPercent: 40,
    ...overrides,
  } as Parameters<typeof diskHealth>[0];
}

test("an uncached or missing snapshot stays unknown so no false alarm fires", () => {
  assert.equal(diskHealth(null), "unknown");
  assert.equal(diskHealth(undefined), "unknown");
  assert.equal(diskHealth(snapshot({ cached: false })), "unknown");
  assert.equal(
    diskHealth(snapshot({ availableGb: null, usedPercent: null })),
    "unknown",
  );
});

test("free space decides before the used percentage", () => {
  assert.equal(diskHealth(snapshot()), "ok");
  assert.equal(diskHealth(snapshot({ availableGb: 12 })), "low");
  assert.equal(diskHealth(snapshot({ availableGb: 4 })), "critical");
  // A nearly full but very large array still has room for an update.
  assert.equal(diskHealth(snapshot({ availableGb: 400, usedPercent: 96 })), "ok");
});

test("the used percentage still catches hosts that report no free bytes", () => {
  assert.equal(diskHealth(snapshot({ availableGb: null, usedPercent: 91 })), "low");
  assert.equal(diskHealth(snapshot({ availableGb: null, usedPercent: 99 })), "critical");
});

test("tones map to the badge vocabulary", () => {
  assert.equal(diskHealthTone("ok"), "ok");
  assert.equal(diskHealthTone("low"), "warn");
  assert.equal(diskHealthTone("critical"), "danger");
  assert.equal(diskHealthTone("unknown"), "neutral");
});
