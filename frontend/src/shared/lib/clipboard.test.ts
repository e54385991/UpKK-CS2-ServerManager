import assert from "node:assert/strict";
import test from "node:test";
import { copyTextFallback, selectElementText } from "./clipboard.ts";

test("copyTextFallback returns false without a document", () => {
  assert.equal(copyTextFallback(" /home/steam/cs2 ", undefined), false);
});

test("copyTextFallback writes through execCommand", () => {
  const removed: string[] = [];
  const area = {
    value: "",
    style: {} as Record<string, string>,
    focus() {},
    select() {},
    setSelectionRange() {},
    setAttribute() {},
    remove() {
      removed.push("removed");
    },
  };
  const doc = {
    body: {
      append(node: { value: string }) {
        assert.equal(node.value, "/home/steam/cs2/game");
      },
    },
    createElement() {
      return area;
    },
    execCommand(command: string) {
      assert.equal(command, "copy");
      return true;
    },
  };
  assert.equal(
    copyTextFallback(
      "/home/steam/cs2/game",
      doc as unknown as Parameters<typeof copyTextFallback>[1],
    ),
    true,
  );
  assert.deepEqual(removed, ["removed"]);
});

test("selectElementText selects the node contents", () => {
  const added: unknown[] = [];
  const range = {
    selectNodeContents(node: { id: string }) {
      assert.equal(node.id, "cmd");
    },
  };
  const selection = {
    removeAllRanges() {},
    addRange(next: typeof range) {
      added.push(next);
    },
  };
  const doc = {
    createRange() {
      return range;
    },
  };
  assert.equal(
    selectElementText(
      { id: "cmd" } as unknown as HTMLElement,
      doc as unknown as Parameters<typeof selectElementText>[1],
      selection,
    ),
    true,
  );
  assert.equal(added.length, 1);
  assert.equal(selectElementText(null), false);
});
