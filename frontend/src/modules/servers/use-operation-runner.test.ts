import assert from "node:assert/strict";
import test from "node:test";
import {
  OPERATION_EVENT_LIMIT,
  lastEventSequence,
  mergeOperationEvents,
  nextReconnectDelayMs,
  operationEventsUrl,
} from "./operation-events.ts";
import type { OperationStreamEvent } from "./types.ts";

function event(sequence: number, message: string): OperationStreamEvent {
  return {
    sequence: String(sequence),
    operationId: "op-1",
    type: "progress",
    kind: "output",
    message,
    timestamp: "2026-08-29T00:00:00.000Z",
  };
}

test("lastEventSequence skips seed and empty ids", () => {
  assert.equal(lastEventSequence([]), "0");
  assert.equal(
    lastEventSequence([
      { sequence: "seed" },
      { sequence: "12" },
      { sequence: "" },
    ]),
    "12",
  );
});

test("operationEventsUrl carries the last sequence", () => {
  assert.equal(
    operationEventsUrl(35, "op-1", "12"),
    "/ops-stream/servers/35/operations/op-1?after=12",
  );
});

test("nextReconnectDelayMs backs off then caps", () => {
  assert.equal(nextReconnectDelayMs(0), 400);
  assert.equal(nextReconnectDelayMs(4), 6400);
  assert.equal(nextReconnectDelayMs(8), 6400);
});

test("mergeOperationEvents keeps only the latest 300 lines", () => {
  assert.equal(OPERATION_EVENT_LIMIT, 300);
  const current = Array.from({ length: 298 }, (_, index) =>
    event(index + 1, `old-${index + 1}`),
  );
  const merged = mergeOperationEvents(current, [
    event(299, "keep-a"),
    event(300, "keep-b"),
    event(301, "keep-c"),
  ]);
  assert.equal(merged.length, 300);
  assert.equal(merged[0]?.message, "old-2");
  assert.equal(merged.at(-1)?.message, "keep-c");
});
