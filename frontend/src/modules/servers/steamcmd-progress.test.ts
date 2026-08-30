import assert from "node:assert/strict";
import test from "node:test";
import { latestSteamcmdProgress } from "./steamcmd-progress.ts";

test("latestSteamcmdProgress prefers the highest downloaded bytes", () => {
  const snapshot = [
    "Update state (0x61) downloading, progress: 10.17 (7232318229 / 71089554542)",
    "Update state (0x61) downloading,",
    "progress: 50.64 (35999207698 / 71089554542)",
  ].join("\n");
  assert.equal(
    latestSteamcmdProgress(snapshot),
    "Update state (0x61) downloading, progress: 50.64 (35999207698 / 71089554542)",
  );
});
