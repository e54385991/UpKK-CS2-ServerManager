import assert from "node:assert/strict";
import test from "node:test";
import {
  parseAssistantSseData,
  readAssistantError,
  toConversation,
  toWorkspace,
  toolFromApprovalPayload,
} from "./assistant-wire.ts";

test("toConversation keeps the bound server id", () => {
  const mapped = toConversation({
    id: "conv-1",
    server_id: 42,
    title: "New conversation",
  });
  assert.equal(mapped.id, "conv-1");
  assert.equal(mapped.serverId, 42);
  assert.equal(mapped.title, "New conversation");
});

test("toWorkspace maps provider flags and conversations", () => {
  const mapped = toWorkspace({
    provider_ready: true,
    mode: "global",
    model: "gpt-4.1",
    conversations: [{ id: "c1", server_id: null, title: "General" }],
  });
  assert.equal(mapped.providerReady, true);
  assert.equal(mapped.mode, "global");
  assert.equal(mapped.conversations[0]?.serverId, null);
});

test("parseAssistantSseData reads named events and payload", () => {
  const event = parseAssistantSseData(
    JSON.stringify({
      sequence: "1",
      type: "run_completed",
      payload: { status: "completed" },
    }),
  );
  assert.deepEqual(event, { type: "run_completed", payload: { status: "completed" } });
  assert.equal(parseAssistantSseData("not-json"), null);
});

test("toolFromApprovalPayload maps the web approval card fields", () => {
  const tool = toolFromApprovalPayload({
    tool_run_id: "tool-1",
    tool_name: "run_server_operation",
    arguments_hash: "a".repeat(64),
    risk: "write",
    arguments: { operation: "update" },
    summary: { title: "Update the bound server" },
  });
  assert.ok(tool);
  assert.equal(tool?.toolName, "run_server_operation");
  assert.equal(tool?.id, "tool-1");
  assert.deepEqual(tool?.arguments, { operation: "update" });
  assert.equal(toolFromApprovalPayload({ tool_name: "list_servers" }), null);
});

test("readAssistantError prefers FastAPI detail strings", () => {
  assert.equal(
    readAssistantError({ detail: "AI Agent is disabled for this server" }, 403),
    "AI Agent is disabled for this server",
  );
  assert.equal(readAssistantError({ detail: [{ msg: "content required" }] }, 422), "content required");
  assert.equal(readAssistantError("<html>", 404), "Request failed with 404");
});
