import assert from "node:assert/strict";
import test from "node:test";
import { mergeTools } from "./assistant-tools.ts";
import type { AssistantTool } from "./types.ts";

function tool(id: string, args: Record<string, unknown> = {}): AssistantTool {
  return {
    id,
    toolName: "run",
    risk: "high",
    status: "pending_approval",
    requiresApproval: true,
    error: null,
    summary: null,
    arguments: args,
    argumentsHash: id,
  };
}

test("mergeTools appends new approvals and fills empty arguments", () => {
  const first = tool("a");
  const filled = tool("a", { path: "/tmp" });
  const extra = tool("b", { x: 1 });
  assert.deepEqual(mergeTools([first], [filled, extra]).map((item) => item.id), ["a", "b"]);
  assert.deepEqual(mergeTools([first], [filled])[0]?.arguments, { path: "/tmp" });
});
