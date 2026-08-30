import assert from "node:assert/strict";
import test from "node:test";
import { hasShortcutPath, parseCustomShortcuts, shortcutLabelFromPath } from "./shortcuts.ts";

test("parseCustomShortcuts keeps valid entries and drops junk", () => {
  const items = parseCustomShortcuts([
    { id: "a", label: " cfg ", path: "/home/steam/cs2/game/csgo/cfg/" },
    { id: "", label: "bad", path: "/x" },
    { label: "missing-id", path: "/x" },
    "nope",
  ]);
  assert.equal(items.length, 1);
  assert.equal(items[0]?.label, "cfg");
  assert.equal(items[0]?.path, "/home/steam/cs2/game/csgo/cfg");
});

test("shortcut helpers", () => {
  assert.equal(shortcutLabelFromPath("/home/steam/cs2/game/csgo/cfg"), "cfg");
  assert.equal(
    hasShortcutPath([{ id: "1", label: "cfg", path: "/home/steam/cs2/game/csgo/cfg" }], "/home/steam/cs2/game/csgo/cfg/"),
    true,
  );
});
