import assert from "node:assert/strict";
import test from "node:test";
import {
  formatRuntimeLines,
  nodeVersion,
  runtimeEnvironment,
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

test("formatRuntimeLines keeps env first and fills missing backend versions", () => {
  const lines = formatRuntimeLines(
    {
      environment: "production",
      node: "22.19.0",
      next: "16.3.3",
      react: "19.2.8",
      python: null,
      fastapi: null,
    },
    {
      environmentProduction: "生产",
      environmentDevelopment: "开发",
      unavailable: "—",
    },
  );
  assert.deepEqual(
    lines.map((line) => `${line.label} ${line.value}`.trim()),
    ["生产", "Node 22.19.0", "Next 16.3.3", "React 19.2.8", "Python —", "FastAPI —"],
  );
});
