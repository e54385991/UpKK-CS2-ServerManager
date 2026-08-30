import assert from "node:assert/strict";
import test from "node:test";
import {
  parsePluginStatusLog,
  pluginRunIsBusy,
  pluginStatusTone,
} from "./status.ts";

test("plugin status logs keep the webpage time + message shape", () => {
  assert.deepEqual(
    parsePluginStatusLog("2026-08-30T12:00:00.000Z started MetaMod"),
    { time: "2026-08-30T12:00:00.000Z", message: "started MetaMod" },
  );
  assert.deepEqual(parsePluginStatusLog("plain line"), {
    time: null,
    message: "plain line",
  });
});

test("plugin run busy lock matches the webpage running state", () => {
  assert.equal(pluginRunIsBusy("running"), true);
  assert.equal(pluginRunIsBusy("completed"), false);
  assert.equal(pluginRunIsBusy("idle"), false);
  assert.equal(pluginStatusTone("running"), "primary");
  assert.equal(pluginStatusTone("completed"), "ok");
  assert.equal(pluginStatusTone("failed"), "danger");
});
