import assert from "node:assert/strict";
import test from "node:test";
import { formatA2SDuration } from "./a2s.ts";

test("formatA2SDuration uses mm:ss and h:mm:ss", () => {
  assert.equal(formatA2SDuration(90.5), "1:30");
  assert.equal(formatA2SDuration(3661), "1:01:01");
  assert.equal(formatA2SDuration(0), "0:00");
});
