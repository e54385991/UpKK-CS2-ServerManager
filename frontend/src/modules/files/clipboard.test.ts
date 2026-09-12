import assert from "node:assert/strict";
import test from "node:test";
import { MAX_FILE_MUTATION_PATHS, parseFileClipboard } from "./clipboard.ts";

test("parseFileClipboard keeps up to the mutation path limit", () => {
  assert.deepEqual(parseFileClipboard({ paths: [" /a ", "", 3, "/b"] }), ["/a", "/b"]);
  assert.deepEqual(parseFileClipboard(null), []);
  const many = { paths: Array.from({ length: MAX_FILE_MUTATION_PATHS + 10 }, (_, index) => `/f${index}`) };
  assert.equal(parseFileClipboard(many).length, MAX_FILE_MUTATION_PATHS);
});
