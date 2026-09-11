import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import test from "node:test";

test("operation inbox and activity tray expose completed history", () => {
  const inbox = readFileSync(
    resolve("frontend/src/modules/servers/operation-inbox.ts"),
    "utf8",
  );
  const tray = readFileSync(
    resolve("frontend/src/modules/shell/activity-tray.tsx"),
    "utf8",
  );

  assert.match(inbox, /completed_items/);
  assert.match(inbox, /completedItems/);
  assert.match(inbox, /completedRetentionDays/);
  assert.match(tray, /activityTabCompleted/);
  assert.match(tray, /clearCompletedOperationsFromBrowser/);
  assert.match(tray, /dismissCompletedOperationFromBrowser/);
});
