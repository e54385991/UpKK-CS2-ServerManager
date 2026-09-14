import assert from "node:assert/strict";
import test from "node:test";
import {
  refreshSharedVisiblePoll,
  resetSharedVisiblePollsForTests,
  snapshotIsFresh,
  subscribeSharedVisiblePoll,
  subscribeVisiblePoll,
  type VisiblePollHost,
} from "./visible-poll.ts";

function hostHarness() {
  let hidden = false;
  let now = 1_000;
  let visibility: (() => void) | null = null;
  const timers = new Map<number, { fn: () => void; at: number }>();
  let nextTimer = 1;
  const host: VisiblePollHost = {
    hidden: () => hidden,
    now: () => now,
    onVisibilityChange: (listener) => {
      visibility = listener;
      return () => {
        if (visibility === listener) visibility = null;
      };
    },
    schedule: (fn, ms) => {
      const id = nextTimer;
      nextTimer += 1;
      timers.set(id, { fn, at: now + ms });
      return id;
    },
    cancel: (id) => {
      timers.delete(id);
    },
  };
  return {
    host,
    setHidden(value: boolean) {
      hidden = value;
      visibility?.();
    },
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

test("snapshotIsFresh is true only inside the refresh window", () => {
  assert.equal(snapshotIsFresh(0, 8_000, 8_000), false);
  assert.equal(snapshotIsFresh(1_000, 8_999, 8_000), true);
  assert.equal(snapshotIsFresh(1_000, 9_000, 8_000), false);
});

test("subscribeVisiblePoll keeps a single in-flight pull", async () => {
  const harness = hostHarness();
  let started = 0;
  let release: (() => void) | null = null;
  const { stop } = subscribeVisiblePoll({
    intervalMs: 1_000,
    host: harness.host,
    pull: () => {
      started += 1;
      return new Promise<number>((resolve) => {
        release = () => resolve(started);
      });
    },
    onResult: () => {},
  });
  await Promise.resolve();
  harness.advance(1_000);
  harness.advance(1_000);
  assert.equal(started, 1);
  release?.();
  await Promise.resolve();
  stop();
});

test("subscribeVisiblePoll pauses while hidden and pulls when visible", async () => {
  const harness = hostHarness();
  const pulls: string[] = [];
  const { stop } = subscribeVisiblePoll({
    intervalMs: 1_000,
    host: harness.host,
    pull: async () => {
      pulls.push(harness.host.hidden() ? "hidden" : "visible");
      return pulls.length;
    },
    onResult: () => {},
  });
  await Promise.resolve();
  assert.deepEqual(pulls, ["visible"]);
  harness.setHidden(true);
  harness.advance(5_000);
  assert.deepEqual(pulls, ["visible"]);
  harness.setHidden(false);
  await Promise.resolve();
  assert.deepEqual(pulls, ["visible", "visible"]);
  stop();
});

test("subscribeVisiblePoll ignores a result after stop", async () => {
  const harness = hostHarness();
  let release: ((value: number) => void) | null = null;
  let received = 0;
  const { stop } = subscribeVisiblePoll({
    intervalMs: 5_000,
    host: harness.host,
    pull: () =>
      new Promise<number>((resolve) => {
        release = resolve;
      }),
    onResult: (value) => {
      received = value;
    },
  });
  await Promise.resolve();
  stop();
  release?.(7);
  await Promise.resolve();
  assert.equal(received, 0);
});

test("subscribeVisiblePoll skips a timer tick when the snapshot is still fresh", async () => {
  const harness = hostHarness();
  let pulls = 0;
  const { stop } = subscribeVisiblePoll({
    intervalMs: 8_000,
    host: harness.host,
    shouldPull: () => !snapshotIsFresh(2_000, harness.host.now(), 8_000),
    pull: async () => {
      pulls += 1;
      return pulls;
    },
    onResult: () => {},
  });
  await Promise.resolve();
  assert.equal(pulls, 1);
  harness.advance(8_000);
  await Promise.resolve();
  assert.equal(pulls, 1);
  stop();
});

test("subscribeSharedVisiblePoll shares one pull until the last listener leaves", async () => {
  resetSharedVisiblePollsForTests();
  const harness = hostHarness();
  let pulls = 0;
  const seen: number[] = [];
  const stopA = subscribeSharedVisiblePoll({
    key: "9:steamcmd",
    intervalMs: 5_000,
    host: harness.host,
    pull: async () => {
      pulls += 1;
      return pulls;
    },
    onResult: (value) => {
      seen.push(value);
    },
  });
  const stopB = subscribeSharedVisiblePoll({
    key: "9:steamcmd",
    intervalMs: 5_000,
    host: harness.host,
    pull: async () => {
      pulls += 10;
      return pulls;
    },
    onResult: (value) => {
      seen.push(value);
    },
  });
  await Promise.resolve();
  assert.equal(pulls, 1);
  assert.deepEqual(seen, [1, 1]);
  stopA();
  stopB();
  await Promise.resolve();
  const stopC = subscribeSharedVisiblePoll({
    key: "9:steamcmd",
    intervalMs: 5_000,
    host: harness.host,
    pull: async () => {
      pulls += 1;
      return pulls;
    },
    onResult: () => {},
  });
  await Promise.resolve();
  assert.equal(pulls, 2);
  stopC();
  resetSharedVisiblePollsForTests();
});

test("subscribeSharedVisiblePoll keeps one in-flight pull across a same-turn remount", async () => {
  resetSharedVisiblePollsForTests();
  const harness = hostHarness();
  let pulls = 0;
  let release: (() => void) | null = null;
  const pull = () => {
    pulls += 1;
    return new Promise<number>((resolve) => {
      release = () => resolve(pulls);
    });
  };
  const stopA = subscribeSharedVisiblePoll({
    key: "activity-tray:inbox",
    intervalMs: 8_000,
    host: harness.host,
    pull,
    onResult: () => {},
  });
  await Promise.resolve();
  assert.equal(pulls, 1);
  stopA();
  const stopB = subscribeSharedVisiblePoll({
    key: "activity-tray:inbox",
    intervalMs: 8_000,
    host: harness.host,
    pull,
    onResult: () => {},
  });
  refreshSharedVisiblePoll("activity-tray:inbox");
  await Promise.resolve();
  assert.equal(pulls, 1);
  release?.();
  stopB();
  resetSharedVisiblePollsForTests();
});
