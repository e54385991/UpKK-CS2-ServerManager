import assert from "node:assert/strict";
import test from "node:test";
import { pipeUnbuffered } from "./stream-proxy.ts";

test("stream proxy closes cleanly when an upstream stream is cancelled", async () => {
  const upstream = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.error(new Error("reading: context canceled"));
    },
  });
  const reader = pipeUnbuffered(upstream).getReader();

  assert.deepEqual(await reader.read(), { done: true, value: undefined });
});

test("stream proxy still surfaces unexpected upstream failures", async () => {
  const upstream = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.error(new Error("upstream failed"));
    },
  });
  const reader = pipeUnbuffered(upstream).getReader();

  await assert.rejects(reader.read(), /upstream failed/);
});
