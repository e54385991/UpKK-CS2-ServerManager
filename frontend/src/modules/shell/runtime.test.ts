import assert from "node:assert/strict";
import test from "node:test";
import {
  formatBuildTime,
  formatRuntimeLines,
  nodeVersion,
  runtimeEnvironment,
  shortCommit,
} from "./runtime.ts";

test("runtimeEnvironment maps NODE_ENV", () => {
  assert.equal(runtimeEnvironment("production"), "production");
  assert.equal(runtimeEnvironment("development"), "development");
  assert.equal(runtimeEnvironment(undefined), "development");
});

test("nodeVersion strips the v prefix", () => {
  assert.equal(nodeVersion("v22.19.0"), "22.19.0");
  assert.equal(nodeVersion("22.19.0"), "22.19.0");
});

test("build metadata is normalized for safe compact display", () => {
  assert.equal(shortCommit("ABCDEF1234567890"), "abcdef1");
  assert.equal(shortCommit("unknown"), "");
  assert.equal(shortCommit("not-a-sha"), "");
  assert.equal(formatBuildTime("2026-09-02T03:04:05Z"), "2026-09-02 03:04:05 UTC");
  assert.equal(formatBuildTime("2026-09-02T03:04:05"), "");
  assert.equal(formatBuildTime("unknown"), "");
  assert.equal(formatBuildTime("invalid"), "");
});

test("formatRuntimeLines keeps env first and fills missing backend versions", () => {
  const lines = formatRuntimeLines(
    {
      environment: "production",
      frontend: {
        version: "0.1.0",
        commit: "abcdef1234567890",
        buildTime: "2026-09-02T03:04:05Z",
      },
      backend: {
        version: "1.0.0",
        commit: null,
        buildTime: null,
      },
      node: "22.19.0",
      next: "16.3.3",
      react: "19.2.8",
      python: null,
      fastapi: null,
    },
    {
      frontend: "前端",
      backend: "后端",
      version: "版本",
      commit: "提交",
      buildTime: "构建",
      environmentProduction: "生产",
      environmentDevelopment: "开发",
      unavailable: "—",
    },
  );
  assert.deepEqual(
    lines.map((line) => `${line.label} ${line.value}`.trim()),
    [
      "生产",
      "前端 版本 0.1.0 · 提交 abcdef1 · 构建 2026-09-02 03:04:05 UTC",
      "后端 版本 1.0.0 · 提交 — · 构建 —",
      "Node 22.19.0",
      "Next 16.3.3",
      "React 19.2.8",
      "Python —",
      "FastAPI —",
    ],
  );
});
