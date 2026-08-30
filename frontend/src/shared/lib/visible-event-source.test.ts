import assert from "node:assert/strict";
import test from "node:test";
import {
  subscribeVisibleEventSource,
  type EventSourceLike,
  type VisibleEventSourceHost,
} from "./visible-event-source.ts";

class FakeSource implements EventSourceLike {
  onopen: ((event: Event) => void) | null = null;
  onmessage: ((event: MessageEvent<string>) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  closed = false;
  readonly url: string;
  readonly listeners = new Map<string, Array<(event: MessageEvent<string>) => void>>();

  constructor(url: string) {
    this.url = url;
  }

  addEventListener(type: string, listener: (event: MessageEvent<string>) => void) {
    const list = this.listeners.get(type) ?? [];
    list.push(listener);
    this.listeners.set(type, list);
  }

  close() {
    this.closed = true;
  }
}

function hostHarness() {
  let hidden = false;
  let visibility: (() => void) | null = null;
  const sources: FakeSource[] = [];
  const timers = new Map<number, () => void>();
  let nextTimer = 1;
  const host: VisibleEventSourceHost = {
    hidden: () => hidden,
    onVisibilityChange: (listener) => {
      visibility = listener;
      return () => {
        if (visibility === listener) visibility = null;
      };
    },
    eventSource: (url) => {
      const source = new FakeSource(url);
      sources.push(source);
      return source;
    },
    delayMs: () => 10,
    schedule: (fn) => {
      const id = nextTimer;
      nextTimer += 1;
      timers.set(id, fn);
      return id;
    },
    cancel: (id) => {
      timers.delete(id);
    },
  };
  return {
    host,
    sources,
    setHidden(value: boolean) {
      hidden = value;
      visibility?.();
    },
    flushTimers() {
      for (const fn of [...timers.values()]) fn();
      timers.clear();
    },
  };
}

test("subscribeVisibleEventSource opens once and closes when hidden", () => {
  const harness = hostHarness();
  const stop = subscribeVisibleEventSource({
    url: "/ops-stream/operations/inbox",
    onData: () => {},
    host: harness.host,
  });
  assert.equal(harness.sources.length, 1);
  assert.equal(harness.sources[0]?.closed, false);
  harness.setHidden(true);
  assert.equal(harness.sources[0]?.closed, true);
  harness.setHidden(false);
  assert.equal(harness.sources.length, 2);
  assert.equal(harness.sources[1]?.closed, false);
  stop();
  assert.equal(harness.sources[1]?.closed, true);
});

test("subscribeVisibleEventSource closes before reconnect so sockets do not stack", () => {
  const harness = hostHarness();
  subscribeVisibleEventSource({
    url: "/ops-stream/operations/inbox",
    onData: () => {},
    host: harness.host,
  });
  const first = harness.sources[0];
  first?.onerror?.(new Event("error"));
  assert.equal(first?.closed, true);
  assert.equal(harness.sources.length, 1);
  harness.flushTimers();
  assert.equal(harness.sources.length, 2);
  assert.equal(harness.sources[1]?.closed, false);
});

test("subscribeVisibleEventSource skips reconnect when the stream is finished", () => {
  const harness = hostHarness();
  let unavailable = 0;
  subscribeVisibleEventSource({
    url: "/ops-stream/operations/inbox",
    onData: () => {},
    shouldReconnect: () => false,
    onUnavailable: () => {
      unavailable += 1;
    },
    host: harness.host,
  });
  harness.sources[0]?.onerror?.(new Event("error"));
  harness.flushTimers();
  assert.equal(unavailable, 1);
  assert.equal(harness.sources.length, 1);
});
