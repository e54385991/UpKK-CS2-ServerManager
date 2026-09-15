import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import test from "node:test";

test("operation inbox and activity tray expose completed history", () => {
  const inbox = readFileSync(
    resolve("frontend/src/modules/servers/operation-inbox.ts"),
    "utf8",
  );
  const panel = readFileSync(
    resolve("frontend/src/modules/shell/activity-tray-panel.tsx"),
    "utf8",
  );
  const commands = readFileSync(
    resolve("frontend/src/modules/shell/use-activity-commands.ts"),
    "utf8",
  );

  assert.match(inbox, /completed_items/);
  assert.match(inbox, /completedItems/);
  assert.match(inbox, /completedRetentionDays/);
  assert.match(panel, /activityTabCompleted/);
  assert.match(commands, /clearCompletedOperationsFromBrowser/);
  assert.match(commands, /dismissCompletedOperationFromBrowser/);
});
