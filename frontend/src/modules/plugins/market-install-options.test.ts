import assert from "node:assert/strict";
import test from "node:test";
import {
  installOptionDefaults,
  pickDefaultAssetIndex,
  pluginTrackedOnServer,
  shouldRefreshInstallDefaults,
} from "./market-install-options.ts";

test("fresh install on a server without the plugin checks dependencies", () => {
  assert.equal(pluginTrackedOnServer([2, 9], 4), false);
  assert.deepEqual(installOptionDefaults(false), {
    upgradeMode: false,
    installDependencies: true,
  });
});

test("reinstall on a server that already has the plugin checks upgrade mode", () => {
  assert.equal(pluginTrackedOnServer([2, 4, 9], 4), true);
  assert.deepEqual(installOptionDefaults(true), {
    upgradeMode: true,
    installDependencies: false,
  });
});

test("presence lookup only refreshes defaults when the optimistic guess was wrong", () => {
  assert.equal(shouldRefreshInstallDefaults(false, false), false);
  assert.equal(shouldRefreshInstallDefaults(false, true), true);
});

test("plain archives auto-select the first asset", () => {
  assert.equal(
    pickDefaultAssetIndex([{ runtimeCompatibility: "not_applicable" }]),
    0,
  );
});

test("paired runtime assets stay unselected until one is recommended", () => {
  assert.equal(
    pickDefaultAssetIndex([
      { runtimeCompatibility: "unknown" },
      { runtimeCompatibility: "alternative" },
    ]),
    null,
  );
  assert.equal(
    pickDefaultAssetIndex([
      { runtimeCompatibility: "recommended" },
      { runtimeCompatibility: "alternative" },
    ]),
    0,
  );
});
