import assert from "node:assert/strict";
import test from "node:test";
import { applyIfCurrent, RequestGate } from "./files-request-gate.ts";

test("only the current request may apply a directory or editor result", () => {
  const gate = new RequestGate();
  const first = gate.next();
  const second = gate.next();
  const applied: number[] = [];

  assert.equal(
    applyIfCurrent(gate, first, () => {
      applied.push(first);
      return "stale";
    }),
    undefined,
  );
  assert.equal(
    applyIfCurrent(gate, second, () => {
      applied.push(second);
      return "current";
    }),
    "current",
  );
  assert.deepEqual(applied, [second]);
});

test("invalidate drops in-flight requests after hide, navigate, or unmount", () => {
  const gate = new RequestGate();
  const requestId = gate.next();
  gate.invalidate();
  assert.equal(gate.isCurrent(requestId), false);
  const next = gate.next();
  assert.equal(gate.isCurrent(next), true);
});
