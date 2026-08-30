import assert from "node:assert/strict";
import test from "node:test";
import {
  GAME_UPDATE_INTERVALS,
  clampPluginInterval,
  matchGameInterval,
  pluginUpdateProgressPercent,
} from "./intervals.ts";

test("game intervals match the webpage option values", () => {
  assert.deepEqual(
    GAME_UPDATE_INTERVALS.map((item) => item.value),
    [0.0167, 0.0333, 0.05, 0.0833, 0.1667, 0.25, 0.5, 1, 2, 3, 4, 6, 8, 12, 24],
  );
  assert.equal(matchGameInterval(3), 3);
  assert.equal(matchGameInterval(0.1667), 0.1667);
  assert.equal(matchGameInterval(7), null);
});

test("saved game intervals are not snapped to a neighbor", () => {
  assert.equal(matchGameInterval(3), 3);
  assert.notEqual(matchGameInterval(3), 2);
  assert.notEqual(matchGameInterval(3), 4);
});

test("plugin interval stays inside the webpage 0.0167–24h range", () => {
  assert.equal(clampPluginInterval(6, 1), 6);
  assert.equal(clampPluginInterval(0, 1), 0.0167);
  assert.equal(clampPluginInterval(48, 1), 24);
  assert.equal(clampPluginInterval(Number.NaN, 2), 2);
});

test("plugin progress matches the webpage percent helper", () => {
  assert.equal(pluginUpdateProgressPercent(0, 0, "idle"), 0);
  assert.equal(pluginUpdateProgressPercent(0, 0, "completed"), 100);
  assert.equal(pluginUpdateProgressPercent(1, 4, "running"), 25);
});
