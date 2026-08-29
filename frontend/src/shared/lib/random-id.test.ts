import assert from "node:assert/strict";
import test from "node:test";
import { randomId, uuidFromBytes } from "./random-id.ts";

const UUID_RE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

test("uuidFromBytes marks version 4 and RFC 4122 variant", () => {
  const bytes = Uint8Array.from({ length: 16 }, (_, index) => index);
  const id = uuidFromBytes(bytes);
  assert.match(id, UUID_RE);
});

test("randomId returns a unique non-empty id", () => {
  const first = randomId();
  const second = randomId();
  assert.notEqual(first, "");
  assert.notEqual(first, second);
});
