import assert from "node:assert/strict";
import test from "node:test";
import { archiveExtensionLabel, isArchiveFile } from "./types.ts";
import {
  breadcrumbs,
  COMMON_FILE_SHORTCUTS,
  filesHref,
  isAtRoot,
  isMissingPathError,
  joinUnderRoot,
  parentPath,
  parentWithinRoot,
  relativeFromRoot,
  renameSelectionEnd,
  resolveJumpPath,
} from "./paths.ts";

const root = "/home/steam/cs2";

test("isMissingPathError ignores connection failures", () => {
  assert.equal(isMissingPathError("No such file"), true);
  assert.equal(isMissingPathError("SFTPError: not a directory"), true);
  assert.equal(isMissingPathError("Connection refused"), false);
});

test("resolveJumpPath treats empty input as the game root", () => {
  assert.equal(resolveJumpPath(root, `${root}/game`, "  "), root);
});

test("resolveJumpPath keeps absolute paths and trims a trailing slash", () => {
  assert.equal(
    resolveJumpPath(root, root, "/home/steam/cs2/game/csgo/"),
    "/home/steam/cs2/game/csgo",
  );
});

test("resolveJumpPath joins a relative draft onto the current directory", () => {
  assert.equal(
    resolveJumpPath(root, `${root}/game/csgo`, "addons/counterstrikesharp"),
    "/home/steam/cs2/game/csgo/addons/counterstrikesharp",
  );
});

test("parentPath stops at the filesystem root", () => {
  assert.equal(parentPath("/home/steam/cs2/game"), "/home/steam/cs2");
  assert.equal(parentPath("/"), "/");
});

test("parentWithinRoot does not walk above the game directory", () => {
  assert.equal(parentWithinRoot(root, `${root}/game/csgo`), `${root}/game`);
  assert.equal(parentWithinRoot(root, `${root}/`), root);
  assert.equal(parentWithinRoot(root, root), root);
  assert.equal(parentWithinRoot(root, "/tmp"), root);
  assert.equal(isAtRoot(root, `${root}/`), true);
});

test("breadcrumbs start at the game root and list each remaining segment", () => {
  const crumbs = breadcrumbs(root, `${root}/game/csgo/addons`);
  assert.deepEqual(
    crumbs.map((crumb) => crumb.name),
    ["cs2", "game", "csgo", "addons"],
  );
  assert.equal(crumbs[0]?.path, root);
  assert.equal(crumbs.at(-1)?.path, `${root}/game/csgo/addons`);
});

test("filesHref omits the query on the game root", () => {
  assert.equal(filesHref(3, root, root), "/servers/3/files");
  assert.equal(filesHref(3, root, `${root}/`), "/servers/3/files");
  assert.equal(
    filesHref(3, root, `${root}/game/csgo`),
    `/servers/3/files?path=${encodeURIComponent(`${root}/game/csgo`)}`,
  );
});

test("joinUnderRoot and relativeFromRoot stay inside the game directory", () => {
  assert.equal(joinUnderRoot(root, "cs2/game/csgo/cfg"), `${root}/cs2/game/csgo/cfg`);
  assert.equal(
    joinUnderRoot("/home/cs2server/cs2ze", "cs2/game/csgo/addons/counterstrikesharp"),
    "/home/cs2server/cs2ze/cs2/game/csgo/addons/counterstrikesharp",
  );
  assert.equal(joinUnderRoot("/home/cs2server/cs2ze", ""), "/home/cs2server/cs2ze");
  assert.equal(joinUnderRoot(root, "../etc"), root);
  assert.equal(relativeFromRoot(root, `${root}/cs2/game/csgo/cfg`), "cs2/game/csgo/cfg");
  assert.equal(relativeFromRoot(root, "/tmp"), null);
});

test("preset shortcuts join onto the overview game directory", () => {
  const base = "/home/cs2server/cs2ze";
  const byId = Object.fromEntries(
    COMMON_FILE_SHORTCUTS.map((item) => [item.id, joinUnderRoot(base, item.relative)]),
  );
  assert.equal(byId.root, base);
  assert.equal(byId.cfg, `${base}/cs2/game/csgo/cfg`);
  assert.equal(byId.css, `${base}/cs2/game/csgo/addons/counterstrikesharp`);
  assert.equal(byId.mam, `${base}/cs2/game/csgo/addons/multiaddonmanager`);
});

test("renameSelectionEnd keeps the extension when renaming a file", () => {
  assert.equal(renameSelectionEnd("server.cfg", false), 6);
  assert.equal(renameSelectionEnd("archive.tar.gz", false), 11);
  assert.equal(renameSelectionEnd("addons", true), 6);
  assert.equal(renameSelectionEnd(".env", false), 4);
});

test("isArchiveFile recognizes rar, xz, zst, and compound tar suffixes", () => {
  assert.equal(isArchiveFile("plugin.rar"), true);
  assert.equal(isArchiveFile("server.tar.zst"), true);
  assert.equal(isArchiveFile("notes.xz"), true);
  assert.equal(isArchiveFile("readme.txt"), false);
  assert.equal(archiveExtensionLabel("CS2Fixes-linux.tar.zst"), "tar.zst");
});
