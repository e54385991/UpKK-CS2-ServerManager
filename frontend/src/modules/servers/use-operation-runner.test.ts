import assert from "node:assert/strict";
import test from "node:test";
import {
  OPERATION_EVENT_LIMIT,
  mergeOperationEvents,
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
