import assert from "node:assert/strict";
import test from "node:test";
import {
  installOptionDefaults,
  pluginTrackedOnServer,
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
