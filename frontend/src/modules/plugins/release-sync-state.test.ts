import assert from "node:assert/strict";
import test from "node:test";
import type { ReleaseSyncJob } from "./types.ts";
import {
  ISSUE_TONE,
  isReleaseTaskActive,
  mergeReleaseTask,
  parseReleaseTask,
} from "./release-sync-state.ts";

function makeJob(overrides: Partial<ReleaseSyncJob> = {}): ReleaseSyncJob {
  return {
    operation_id: "op-1",
    mode: "scan",
    status: "running",
    command: "Scan invalid plugins",
    framework: "counterstrikesharp",
    created_at: "2026-11-26T08:00:00Z",
    started_at: "2026-11-26T08:00:01Z",
    completed_at: null,
    phase: "checking",
    message: "Processed 1/3",
    current_plugin_id: null,
    current_plugin_title: null,
    current_github_url: null,
    stop_reason: null,
    retry_at: null,
    cancel_requested: false,
    total: 3,
    processed: 1,
    updated: 0,
    unchanged: 0,
    skipped: 0,
    failed: 1,
    cleanable: 1,
    items: [],
    ...overrides,
  };
}

test("active vs terminal status helpers and monotonic merge", () => {
  const running1 = makeJob({ processed: 1, status: "running" });
  const running2 = makeJob({ processed: 2, status: "running" });
  const completed = makeJob({ processed: 3, status: "completed" });

  assert.equal(isReleaseTaskActive(running1), true);
  assert.equal(isReleaseTaskActive(completed), false);
  assert.equal(mergeReleaseTask(running2, running1).processed, 2);
  assert.equal(mergeReleaseTask(completed, running2).status, "completed");
});

test("parseReleaseTask validates SSE payload shape and issue tones", () => {
  const valid = makeJob({ cleanable: 2 });
  assert.deepEqual(parseReleaseTask(JSON.stringify(valid)), valid);
  assert.equal(parseReleaseTask("not-json"), null);
  assert.equal(parseReleaseTask(JSON.stringify({ operation_id: "x" })), null);
  assert.equal(ISSUE_TONE.repository_missing, "danger");
  assert.equal(ISSUE_TONE.manual_selection_required, "warn");
});
