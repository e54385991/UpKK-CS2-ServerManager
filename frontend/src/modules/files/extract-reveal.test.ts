import assert from "node:assert/strict";
import test from "node:test";
import {
  extractRevealOpenPath,
  guessExtractedFolderName,
  pickRevealedFolder,
  revealDelayMs,
  type ExtractRevealHint,
} from "./extract-reveal.ts";
import { archiveStem } from "./types.ts";

function hint(overrides: Partial<ExtractRevealHint> = {}): ExtractRevealHint {
  return {
    destination: "/tmp/cs2-lan-ops",
    archiveName: "MatchZy.zip",
    stripSourceFolder: false,
    archiveFolders: ["MatchZy", "MatchZy/cfg"],
    ...overrides,
  };
}

function folder(name: string) {
  return { name, type: "directory" as const };
}

test("archiveStem strips compound archive extensions", () => {
  assert.equal(archiveStem("plugin.tar.gz"), "plugin");
  assert.equal(archiveStem("MatchZy.zip"), "MatchZy");
  assert.equal(archiveStem("notes"), "notes");
});

test("guessExtractedFolderName prefers the selected folder when it is kept", () => {
  assert.equal(
    guessExtractedFolderName(
      hint({ sourceFolder: "MatchZy/cfg", stripSourceFolder: false }),
      "MatchZy",
    ),
    "cfg",
  );
  assert.equal(
    guessExtractedFolderName(hint({ sourceFolder: "MatchZy", stripSourceFolder: true }), "MatchZy"),
    null,
  );
});

test("guessExtractedFolderName uses the single top-level archive folder", () => {
  assert.equal(guessExtractedFolderName(hint(), "MatchZy"), "MatchZy");
  assert.equal(
    guessExtractedFolderName(
      hint({ archiveFolders: ["addons", "cfg"], archiveName: "pack.zip" }),
      "pack",
    ),
    "pack",
  );
});

test("pickRevealedFolder and extractRevealOpenPath only follow a real directory", () => {
  const match = folder("MatchZy");
  assert.equal(pickRevealedFolder([match], "MatchZy"), match);
  assert.equal(pickRevealedFolder([match], "other"), null);
  assert.equal(extractRevealOpenPath("/tmp/cs2-lan-ops", match), "/tmp/cs2-lan-ops/MatchZy");
  assert.equal(extractRevealOpenPath("/tmp/cs2-lan-ops", null), null);
});

test("revealDelayMs collapses when motion is reduced", () => {
  assert.equal(revealDelayMs({ matches: true }), 0);
  assert.equal(revealDelayMs({ matches: false }), 720);
});
