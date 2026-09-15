import assert from "node:assert/strict";
import test from "node:test";
import {
  PRODUCTION_SURFACES,
  PRODUCTION_WIDTHS,
  surfacesFromEnv,
  widthsFromEnv,
} from "./production-surfaces.ts";

test("default surfaces match this round's browser matrix", () => {
  assert.deepEqual(
    PRODUCTION_SURFACES.map((item) => item.name),
    [
      "login",
      "overview",
      "servers",
      "activity-tray",
      "plugins-install",
      "assistant",
      "files",
    ],
  );
  assert.deepEqual([...PRODUCTION_WIDTHS], [390, 1440]);
});

test("PERF_BROWSER_ROUTES keeps listed order and rejects unknown names", () => {
  assert.deepEqual(
    surfacesFromEnv({}).map((item) => item.name),
    PRODUCTION_SURFACES.map((item) => item.name),
  );
  assert.deepEqual(
    surfacesFromEnv({ PERF_BROWSER_ROUTES: "files,login" }).map((item) => item.name),
    ["files", "login"],
  );
  assert.throws(
    () => surfacesFromEnv({ PERF_BROWSER_ROUTES: "login,not-a-surface" }),
    /unknown PERF_BROWSER_ROUTES/,
  );
});

test("PERF_WIDTHS stays inside the protocol pair", () => {
  assert.deepEqual(widthsFromEnv({}), [390, 1440]);
  assert.deepEqual(widthsFromEnv({ PERF_WIDTHS: "1440" }), [1440]);
  assert.throws(() => widthsFromEnv({ PERF_WIDTHS: "1280" }), /unknown PERF_WIDTHS/);
});
