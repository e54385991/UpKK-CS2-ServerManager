import assert from "node:assert/strict";
import test from "node:test";
import {
  addPostUpdateCommand,
  availablePostUpdateCommands,
  movePostUpdateCommand,
  removePostUpdateCommand,
} from "./post-commands.ts";

test("post-update commands stay ordered like the webpage list", () => {
  assert.deepEqual(addPostUpdateCommand([3], 8), [3, 8]);
  assert.deepEqual(addPostUpdateCommand([3], 3), [3]);
  assert.deepEqual(removePostUpdateCommand([3, 8, 2], 1), [3, 2]);
  assert.deepEqual(movePostUpdateCommand([3, 8, 2], 1, -1), [8, 3, 2]);
  assert.deepEqual(movePostUpdateCommand([3, 8, 2], 2, 1), [3, 8, 2]);
  assert.deepEqual(
    availablePostUpdateCommands(
      [{ id: 3 }, { id: 8 }, { id: 2 }],
      [8],
    ).map((item) => item.id),
    [3, 2],
  );
});
