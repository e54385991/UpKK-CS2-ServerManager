import assert from "node:assert/strict";
import test from "node:test";
import { WORKSPACE_NAV_PREFETCH } from "./workspace.ts";

test("workspace nav does not prefetch in-viewport SSH pages", () => {
  assert.equal(WORKSPACE_NAV_PREFETCH, false);
});
