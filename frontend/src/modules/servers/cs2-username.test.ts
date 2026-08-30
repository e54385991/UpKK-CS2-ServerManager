import assert from "node:assert/strict";
import test from "node:test";
import {
  CS2_USERNAME_PATTERN,
  isCs2Username,
} from "./cs2-username.ts";

test("accepts typical service usernames", () => {
  assert.equal(isCs2Username("cs2server"), true);
  assert.equal(isCs2Username("cs2_user-1"), true);
});

test("rejects empty or illegal names", () => {
  assert.equal(isCs2Username(""), false);
  assert.equal(isCs2Username("1steam"), false);
  assert.equal(isCs2Username("Steam"), false);
});

test("HTML pattern escapes the hyphen for the unicode-sets flag", () => {
  assert.equal(CS2_USERNAME_PATTERN.includes("\\-"), true);
  assert.match("cs2_user-1", new RegExp(`^${CS2_USERNAME_PATTERN}$`));
});
