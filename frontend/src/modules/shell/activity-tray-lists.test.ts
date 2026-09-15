import assert from "node:assert/strict";
import { describe, it } from "node:test";
import type { OperationInbox, OperationInboxItem } from "../servers/types.ts";
import { deriveActivityLists } from "./activity-tray-lists.ts";

function item(overrides: Partial<OperationInboxItem> & Pick<OperationInboxItem, "operationId" | "status">): OperationInboxItem {
  return {
    serverId: 1,
    action: "start",
    success: null,
    message: null,
    serverStatus: null,
    startedAt: "2026-09-15T00:00:00Z",
    completedAt: null,
    actorUserId: 1,
    streamUrl: "/events",
    command: "start",
    serverName: "alpha",
    latestMessage: "queued",
    queuePosition: 0,
    ...overrides,
  };
}

function inbox(values: Partial<OperationInbox>): OperationInbox {
  return {
    items: [],
    completedItems: [],
    failedItems: [],
    activeCount: 0,
    runningCount: 0,
    completedCount: 0,
    failedCount: 0,
    completedRetentionDays: 7,
    failedRetentionDays: 7,
    ...values,
  };
}

describe("deriveActivityLists", () => {
  it("reuses unchanged item objects after an identical inbox snapshot", () => {
    const queued = item({ operationId: "op-1", status: "queued" });
    const first = deriveActivityLists(inbox({ items: [queued] }), [], new Set());
    const second = deriveActivityLists(
      inbox({ items: [{ ...queued }] }),
      [],
      new Set(),
      first,
    );
    assert.equal(second.queue, first.queue);
    assert.equal(second.queue[0], first.queue[0]);
  });

  it("keeps a new object when status or message changes", () => {
    const queued = item({ operationId: "op-1", status: "queued" });
    const first = deriveActivityLists(inbox({ items: [queued] }), [], new Set());
    const second = deriveActivityLists(
      inbox({ items: [{ ...queued, status: "running", latestMessage: "installing" }] }),
      [],
      new Set(),
      first,
    );
    assert.notEqual(second.queue[0], first.queue[0]);
    assert.equal(second.queue[0]?.status, "running");
    assert.equal(second.queue[0]?.latestMessage, "installing");
  });

  it("sorts running ahead of queued and hides dismissed ids", () => {
    const queued = item({ operationId: "q", status: "queued", startedAt: "2026-09-15T00:02:00Z" });
    const running = item({ operationId: "r", status: "running", startedAt: "2026-09-15T00:01:00Z" });
    const gone = item({ operationId: "gone", status: "queued" });
    const lists = deriveActivityLists(
      inbox({ items: [queued, running, gone] }),
      [],
      new Set(["gone"]),
    );
    assert.deepEqual(lists.queue.map((entry) => entry.operationId), ["r", "q"]);
  });
});
