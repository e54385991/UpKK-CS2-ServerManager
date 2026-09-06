import assert from "node:assert/strict";
import test from "node:test";
import { markdownToPlainText } from "./markdown.ts";

test("markdownToPlainText flattens markup into a one-line excerpt", () => {
  const excerpt = markdownToPlainText(
    [
      "# CS2Fixes",
      "",
      "A **large** set of [fixes](https://x.example).",
      "",
      "- one",
      "- ~~two~~",
    ].join("\n"),
  );
  assert.equal(excerpt, "CS2Fixes A large set of fixes. one two");
});

test("markdownToPlainText keeps identifiers that contain underscores", () => {
  assert.equal(
    markdownToPlainText("use `mp_freezetime` and sv_cheats_1"),
    "use mp_freezetime and sv_cheats_1",
  );
});

test("markdownToPlainText keeps image alt text and drops fenced code", () => {
  const excerpt = markdownToPlainText(
    ["![build passing](https://img.example/b.svg)", "", "```ini", "tickrate=128", "```"].join(
      "\n",
    ),
  );
  assert.equal(excerpt, "build passing");
});

test("markdownToPlainText leaves raw HTML out of the excerpt", () => {
  assert.equal(
    markdownToPlainText("<img src=x onerror=alert(1)> plain tail"),
    "plain tail",
  );
});

test("markdownToPlainText decodes the character references stringify adds", () => {
  assert.equal(markdownToPlainText("Rock & roll <not-a-tag>"), "Rock & roll");
  assert.equal(markdownToPlainText("a & b"), "a & b");
});

test("markdownToPlainText is empty for missing descriptions", () => {
  assert.equal(markdownToPlainText(null), "");
  assert.equal(markdownToPlainText(undefined), "");
  assert.equal(markdownToPlainText(""), "");
  assert.equal(markdownToPlainText("   \n\n "), "");
});
