import assert from "node:assert/strict";
import test from "node:test";
import {
  clearLinkPending,
  ensureLinkPendingCapture,
  getLinkPendingHref,
  isCapturedLinkPending,
  markLinkPending,
  subscribeLinkPending,
} from "./nav-pending.ts";

test("ensureLinkPendingCapture is a no-op without a document", () => {
  assert.doesNotThrow(() => ensureLinkPendingCapture());
});

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

test("captured pending stays true after the router commits the same href", () => {
  clearLinkPending();
  markLinkPending("/servers/1/files");
  assert.equal(isCapturedLinkPending("/servers/1/files", getLinkPendingHref()), true);
  assert.equal(isCapturedLinkPending("/servers/1/console", getLinkPendingHref()), false);
});
