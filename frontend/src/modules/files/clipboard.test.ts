import assert from "node:assert/strict";
import test from "node:test";
import { parseFileClipboard } from "./clipboard.ts";

test("parseFileClipboard keeps up to 50 non-empty paths", () => {
  assert.deepEqual(parseFileClipboard({ paths: [" /a ", "", 3, "/b"] }), ["/a", "/b"]);
  assert.deepEqual(parseFileClipboard(null), []);
  const many = { paths: Array.from({ length: 60 }, (_, index) => `/f${index}`) };
  assert.equal(parseFileClipboard(many).length, 50);
});
