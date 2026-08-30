import assert from "node:assert/strict";
import test from "node:test";
import { providerTestAlert, providerTestErrorAlert } from "./ai-test-result.ts";

const labels: Record<string, string> = {
  testOk: "正常",
  testFail: "失败",
  testText: "文本回复：",
  testTools: "工具调用：",
  testStream: "流式输出：",
  testUsableTitle: "可以正常使用",
  testUnusableTitle: "当前不能稳定使用",
  testUsableHelp: "三项都通过，助手可以正常对话。",
  testUnusableHelp: "未全部通过。仍可打开助手，最多会在对话时报错。",
  testAck: "知道了",
};

function t(key: string): string {
  return labels[key] ?? key;
}

test("providerTestAlert reports a usable provider", () => {
  const alert = providerTestAlert(
    {
      success: true,
      text_response_ok: true,
      tool_calling_ok: true,
      streaming_ok: true,
      message: "ok",
    },
    t,
  );
  assert.equal(alert.tone, "ok");
  assert.equal(alert.title, "可以正常使用");
  assert.match(String(alert.description), /文本回复：正常/);
  assert.match(String(alert.description), /工具调用：正常/);
  assert.match(String(alert.description), /流式输出：正常/);
});

test("providerTestAlert reports a partial failure as unusable", () => {
  const alert = providerTestAlert(
    {
      success: false,
      text_response_ok: true,
      tool_calling_ok: false,
      streaming_ok: true,
      message: "tools missing",
    },
    t,
  );
  assert.equal(alert.tone, "danger");
  assert.equal(alert.title, "当前不能稳定使用");
  assert.match(String(alert.description), /工具调用：失败/);
  assert.match(String(alert.description), /tools missing/);
});

test("providerTestErrorAlert uses the transport error", () => {
  const alert = providerTestErrorAlert("Authentication required", t);
  assert.equal(alert.tone, "danger");
  assert.match(String(alert.description), /Authentication required/);
});
