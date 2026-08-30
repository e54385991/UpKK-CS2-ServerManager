import assert from "node:assert/strict";
import test from "node:test";
import {
  compareEntries,
  filterAndSortEntries,
  highlightName,
  matchesFileQuery,
  matchesKindFilter,
  type FileEntry,
} from "./types.ts";

function entry(
  name: string,
  type: FileEntry["type"],
  extra?: Partial<FileEntry>,
): FileEntry {
  return {
    name,
    path: `/game/${name}`,
    type,
    size: extra?.size ?? 0,
    modified: extra?.modified ?? 0,
    permissions: "644",
    isSymlink: false,
  };
}

test("matchesFileQuery requires every token in the file name", () => {
  const cfg = entry("server.cfg", "file");
  assert.equal(matchesFileQuery(cfg, ""), true);
  assert.equal(matchesFileQuery(cfg, "server"), true);
  assert.equal(matchesFileQuery(cfg, "CFG"), true);
  assert.equal(matchesFileQuery(cfg, "server cfg"), true);
  assert.equal(matchesFileQuery(cfg, "gamemode"), false);
});

test("kind filters split folders, archives, and text", () => {
  assert.equal(matchesKindFilter(entry("addons", "directory"), "folders"), true);
  assert.equal(matchesKindFilter(entry("notes.txt", "file"), "text"), true);
  assert.equal(matchesKindFilter(entry("mod.zip", "file"), "archives"), true);
  assert.equal(matchesKindFilter(entry("mod.zip", "file"), "text"), false);
  assert.equal(matchesKindFilter(entry("addons", "directory"), "files"), false);
});

test("filterAndSortEntries hides dot entries and keeps folders first", () => {
  const listed = filterAndSortEntries(
    [
      entry(".", "directory"),
      entry("z-last.txt", "file", { size: 10 }),
      entry("addons", "directory"),
      entry("a.cfg", "file", { size: 2 }),
    ],
    "",
    "all",
    "name",
    "asc",
  );
  assert.deepEqual(
    listed.map((item) => item.name),
    ["addons", "a.cfg", "z-last.txt"],
  );
});

test("compareEntries can sort files by size without mixing folders", () => {
  const folder = entry("cfg", "directory", { size: 0 });
  const small = entry("a.txt", "file", { size: 1 });
  const large = entry("b.txt", "file", { size: 9 });
  assert.ok(compareEntries(folder, large, "size", "desc") < 0);
  assert.ok(compareEntries(large, small, "size", "desc") < 0);
});

test("highlightName marks every query token", () => {
  const parts = highlightName("Server.cfg", "cfg ser");
  assert.deepEqual(
    parts.map((part) => `${part.match ? "[" : ""}${part.text}${part.match ? "]" : ""}`).join(""),
    "[Ser]ver.[cfg]",
  );
  assert.deepEqual(highlightName("readme", ""), [{ text: "readme", match: false }]);
});
