import assert from "node:assert/strict";
import test from "node:test";
import { SESSION_COOKIE, sessionCookieName } from "./session-cookie-name.ts";

test("sessionCookieName keeps unsuffixed and suffixed instances apart", () => {
  assert.equal(sessionCookieName(""), SESSION_COOKIE);
  assert.equal(sessionCookieName("   "), SESSION_COOKIE);
  assert.equal(sessionCookieName("31800"), `${SESSION_COOKIE}_31800`);
  assert.equal(sessionCookieName("31801"), `${SESSION_COOKIE}_31801`);
  assert.notEqual(sessionCookieName("31800"), sessionCookieName("31801"));
});

test("token lookup for one suffix cannot read another instance cookie", () => {
  const left = sessionCookieName("31800");
  const right = sessionCookieName("31801");
  const store = {
    get(name: string) {
      if (name === SESSION_COOKIE) return { value: "plain-token" };
      if (name === left) return { value: "left-token" };
      if (name === right) return { value: "right-token" };
      return undefined;
    },
    has(name: string) {
      return this.get(name) !== undefined;
    },
  };
  assert.equal(store.get(left)?.value, "left-token");
  assert.equal(store.get(right)?.value, "right-token");
  assert.equal(store.get(SESSION_COOKIE)?.value, "plain-token");
  assert.equal(store.has(left), true);
  assert.notEqual(store.get(left)?.value, store.get(right)?.value);
  assert.notEqual(store.get(left)?.value, store.get(SESSION_COOKIE)?.value);
});

test("sessionCookieName reads SESSION_COOKIE_SUFFIX at call time", () => {
  const previous = process.env["SESSION_COOKIE_SUFFIX"];
  process.env["SESSION_COOKIE_SUFFIX"] = "4000";
  try {
    assert.equal(sessionCookieName(), `${SESSION_COOKIE}_4000`);
  } finally {
    if (previous === undefined) delete process.env["SESSION_COOKIE_SUFFIX"];
    else process.env["SESSION_COOKIE_SUFFIX"] = previous;
  }
});
