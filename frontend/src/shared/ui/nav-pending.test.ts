import assert from "node:assert/strict";
import test from "node:test";
import {
  clearLinkPending,
  getLinkPendingHref,
  markLinkPending,
  subscribeLinkPending,
} from "./nav-pending.ts";

test("markLinkPending replaces the previous target and notifies listeners", () => {
  clearLinkPending();
  const seen: Array<string | null> = [];
  const unsubscribe = subscribeLinkPending(() => seen.push(getLinkPendingHref()));
  markLinkPending("/servers/1/files");
  markLinkPending("/servers/1/console");
  assert.deepEqual(seen, ["/servers/1/files", "/servers/1/console"]);
  assert.equal(getLinkPendingHref(), "/servers/1/console");
  clearLinkPending("/servers/1/files");
  assert.equal(getLinkPendingHref(), "/servers/1/console");
  clearLinkPending("/servers/1/console");
  assert.equal(getLinkPendingHref(), null);
  unsubscribe();
});
