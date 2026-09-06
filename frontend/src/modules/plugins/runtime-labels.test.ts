import assert from "node:assert/strict";
import { test } from "node:test";
import { runtimeMismatchValues } from "./runtime-labels.ts";
import type { PluginFrameworkCompatibility } from "./types.ts";

const label = (key: string) =>
  ({ counterstrikesharp: "CounterStrikeSharp", swiftly: "SwiftlyS2" })[key] ?? key;

function compatibility(
  overrides: Partial<PluginFrameworkCompatibility> = {},
): PluginFrameworkCompatibility {
  return {
    plugin: "counterstrikesharp",
    installed: ["swiftly"],
    conflicting: ["swiftly"],
    missing: true,
    mismatch: true,
    ...overrides,
  };
}

test("runtimeMismatchValues names the server runtime and the plugin runtime", () => {
  assert.deepEqual(runtimeMismatchValues(compatibility(), label), {
    server: "SwiftlyS2",
    plugin: "CounterStrikeSharp",
  });
});

test("runtimeMismatchValues joins several conflicting runtimes", () => {
  const values = runtimeMismatchValues(
    compatibility({ plugin: "swiftly", conflicting: ["counterstrikesharp", "other"] }),
    label,
  );
  assert.equal(values.server, "CounterStrikeSharp, other");
  assert.equal(values.plugin, "SwiftlyS2");
});

test("runtimeMismatchValues stays defined with no conflicting runtime", () => {
  const values = runtimeMismatchValues(compatibility({ conflicting: [] }), label);
  assert.equal(values.server, "");
  assert.equal(values.plugin, "CounterStrikeSharp");
});
