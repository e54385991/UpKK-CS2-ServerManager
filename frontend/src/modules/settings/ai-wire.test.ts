import assert from "node:assert/strict";
import test from "node:test";
import { readAiApiError, toAiSettings, toAiSettingsWire } from "./ai-wire.ts";

test("toAiSettings hides missing optional fields", () => {
  const mapped = toAiSettings({
    enabled: false,
    base_url: "https://api.example.com/v1",
    model: "gpt-4.1",
    api_protocol: "chat_completions",
    api_key_configured: true,
    admin_prompt: null,
    private_endpoint_allowlist: ["http://10.0.0.8:8000"],
    reasoning_effort: null,
    temperature: null,
    top_p: null,
    max_completion_tokens: 2048,
    token_limit_parameter: "max_completion_tokens",
    frequency_penalty: null,
    presence_penalty: null,
    verbosity: null,
    parallel_tool_calls: null,
    request_timeout_seconds: 60,
    history_retention_days: 7,
    max_provider_rounds: 200,
    max_tool_calls_per_round: 200,
    provider_tested: true,
    tool_calling_tested: false,
    streaming_tested: true,
  });
  assert.equal(mapped.baseUrl, "https://api.example.com/v1");
  assert.deepEqual(mapped.privateEndpointAllowlist, ["http://10.0.0.8:8000"]);
  assert.equal(mapped.providerTested, true);
});

test("toAiSettingsWire keeps write-only key and allowlist", () => {
  const wire = toAiSettingsWire({
    enabled: false,
    baseUrl: "https://api.example.com/v1",
    apiKey: "sk-test",
    privateEndpointAllowlist: ["http://10.0.0.8:8000"],
  });
  assert.equal(wire.base_url, "https://api.example.com/v1");
  assert.equal(wire.api_key, "sk-test");
  assert.deepEqual(wire.private_endpoint_allowlist, ["http://10.0.0.8:8000"]);
  assert.equal("clear_api_key" in wire, false);
});

test("readAiApiError prefers FastAPI detail strings", () => {
  assert.equal(readAiApiError({ detail: "Base URL, model, and API key are required" }, 422), "Base URL, model, and API key are required");
  assert.equal(readAiApiError({ detail: [{ msg: "Set temperature or top_p, not both" }] }, 422), "Set temperature or top_p, not both");
  assert.equal(readAiApiError({ message: "Provider test failed" }, 200), "Provider test failed");
  assert.equal(readAiApiError("<html>", 404), "Request failed with 404");
});
