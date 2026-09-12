import assert from "node:assert/strict";
import test from "node:test";
import {
  SERVER_WORKSPACE_CATEGORIES,
  WORKSPACE_NAV_PREFETCH,
  workspaceNavPrefetchOnIntent,
} from "./workspace.ts";

test("workspace nav does not prefetch in-viewport SSH pages", () => {
  assert.equal(WORKSPACE_NAV_PREFETCH, false);
});

test("only game config and host config prefetch after hover or focus", () => {
  assert.equal(SERVER_WORKSPACE_CATEGORIES.length, 19);
  for (const category of SERVER_WORKSPACE_CATEGORIES) {
    assert.equal(
      workspaceNavPrefetchOnIntent(category),
      category === "config" || category === "host-config",
      category,
    );
  }
});
