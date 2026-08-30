import assert from "node:assert/strict";
import test from "node:test";
import { formatA2SDuration, paginateA2SLogs } from "./a2s.ts";

test("paginateA2SLogs shows five newest entries per page", () => {
  const items = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12];
  const first = paginateA2SLogs(items, 0);
  assert.deepEqual(first.items, [1, 2, 3, 4, 5]);
  assert.equal(first.from, 1);
  assert.equal(first.to, 5);
  assert.equal(first.total, 12);
  assert.equal(first.hasPrev, false);
  assert.equal(first.hasNext, true);

  const last = paginateA2SLogs(items, 2);
  assert.deepEqual(last.items, [11, 12]);
  assert.equal(last.from, 11);
  assert.equal(last.to, 12);
  assert.equal(last.hasPrev, true);
  assert.equal(last.hasNext, false);

  const overflow = paginateA2SLogs(items, 99);
  assert.equal(overflow.page, 2);
  assert.deepEqual(overflow.items, [11, 12]);
});

test("formatA2SDuration uses mm:ss and h:mm:ss", () => {
  assert.equal(formatA2SDuration(90.5), "1:30");
  assert.equal(formatA2SDuration(3661), "1:01:01");
  assert.equal(formatA2SDuration(0), "0:00");
});
