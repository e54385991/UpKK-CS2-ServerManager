import assert from "node:assert/strict";
import test from "node:test";
import { toCleanupScan, toCleanupSystemScan } from "./wire.ts";

test("toCleanupScan maps truncated counts and danger level", () => {
  const scan = toCleanupScan({
    safe_items: [
      {
        path: "/a.log",
        name: "a.log",
        type: "file",
        size: 8,
        category: "safe",
        reason: "log file",
        danger_level: "safe",
      },
    ],
    archive_items: [],
    workshop_summary: { path: "/ws", item_count: 3, size: 40 },
    total_size: 48,
    safe_item_count: 90,
    archive_item_count: 4,
    truncated: true,
  });
  assert.equal(scan.safeItems[0]?.dangerLevel, "safe");
  assert.equal(scan.safeItemCount, 90);
  assert.equal(scan.archiveItemCount, 4);
  assert.equal(scan.workshopCount, 3);
  assert.equal(scan.truncated, true);
});

test("toCleanupSystemScan maps privilege flags", () => {
  const scan = toCleanupSystemScan({
    privilege: "sudo",
    retain_days: 7,
    has_sudo_password: true,
    targets: [
      {
        id: "journal",
        title: "journal",
        reason: "vacuum",
        size: 12,
        needs_privilege: true,
        can_apply: true,
        command: "journalctl --vacuum-time=7d",
      },
    ],
    total_size: 12,
    can_apply_privileged: true,
    manual_execute: [],
    manual_setup: [],
  });
  assert.equal(scan.privilege, "sudo");
  assert.equal(scan.targets[0]?.needsPrivilege, true);
  assert.equal(scan.canApplyPrivileged, true);
});
