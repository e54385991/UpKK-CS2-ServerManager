import assert from "node:assert/strict";
import test from "node:test";
import {
  createRenderCoalescer,
  createTextDisplayBuffer,
  isTerminalOperationEventType,
  type RenderCoalesceHost,
} from "./render-coalesce.ts";

function hostHarness() {
  let now = 0;
  const timers = new Map<number, { fn: () => void; at: number }>();
  let nextId = 1;
  const host: RenderCoalesceHost = {
    schedule: (fn, ms) => {
      const id = nextId;
      nextId += 1;
      timers.set(id, { fn, at: now + ms });
      return id;
    },
    cancel: (id) => {
      timers.delete(id);
    },
  };
  return {
    host,
    advance(ms: number) {
      now += ms;
      for (const [id, timer] of [...timers]) {
        if (timer.at <= now) {
          timers.delete(id);
          timer.fn();
        }
      }
    },
    pending() {
      return timers.size;
    },
  };
}

test("createRenderCoalescer merges items until the interval elapses", () => {
  const harness = hostHarness();
  const batches: number[][] = [];
  const coalescer = createRenderCoalescer<number>(
    (batch) => {
      batches.push([...batch]);
    },
    50,
    harness.host,
  );
  coalescer.push(1);
  coalescer.push(2);
  assert.deepEqual(batches, []);
  harness.advance(50);
  assert.deepEqual(batches, [[1, 2]]);
});

test("createRenderCoalescer flushes immediately for terminal items", () => {
  const harness = hostHarness();
  const batches: string[][] = [];
  const coalescer = createRenderCoalescer<string>(
    (batch) => {
      batches.push([...batch]);
    },
    50,
    harness.host,
  );
  coalescer.push("a");
  coalescer.push("done", { immediate: true });
  assert.deepEqual(batches, [["a", "done"]]);
  assert.equal(harness.pending(), 0);
});

test("createRenderCoalescer dispose drops unflushed items", () => {
  const harness = hostHarness();
  const batches: number[][] = [];
  const coalescer = createRenderCoalescer<number>(
    (batch) => {
      batches.push([...batch]);
    },
    50,
    harness.host,
  );
  coalescer.push(1);
  coalescer.dispose();
  harness.advance(50);
  assert.deepEqual(batches, []);
});

test("createTextDisplayBuffer concatenates deltas on the interval", () => {
  const harness = hostHarness();
  const chunks: string[] = [];
  const buffer = createTextDisplayBuffer(
    (chunk) => {
      chunks.push(chunk);
    },
    50,
    harness.host,
  );
  buffer.append("hel");
  buffer.append("lo");
  assert.deepEqual(chunks, []);
  harness.advance(50);
  assert.deepEqual(chunks, ["hello"]);
  buffer.append("!");
  buffer.flush();
  assert.deepEqual(chunks, ["hello", "!"]);
});

test("isTerminalOperationEventType covers completed and failed", () => {
  assert.equal(isTerminalOperationEventType("operation_completed"), true);
  assert.equal(isTerminalOperationEventType("operation_failed"), true);
  assert.equal(isTerminalOperationEventType("progress"), false);
});
