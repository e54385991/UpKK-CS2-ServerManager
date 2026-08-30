import assert from "node:assert/strict";
import test from "node:test";
import { deleteCodeMatches, randomDeleteCode } from "./delete-challenge.ts";

test("randomDeleteCode is a four-digit number", () => {
  for (let index = 0; index < 20; index += 1) {
    const code = randomDeleteCode();
    assert.match(code, /^[1-9]\d{3}$/);
  }
});

test("deleteCodeMatches ignores surrounding spaces", () => {
  assert.equal(deleteCodeMatches(" 4821 ", "4821"), true);
  assert.equal(deleteCodeMatches("4822", "4821"), false);
});
