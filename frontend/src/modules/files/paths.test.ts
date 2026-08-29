import assert from "node:assert/strict";
import test from "node:test";
import { archiveExtensionLabel, isArchiveFile } from "./types.ts";
import {
  breadcrumbs,
  filesHref,
  isAtRoot,
  parentPath,
  parentWithinRoot,
  resolveJumpPath,
} from "./paths.ts";

const root = "/home/steam/cs2";

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

test("isArchiveFile recognizes rar, xz, zst, and compound tar suffixes", () => {
  assert.equal(isArchiveFile("plugin.rar"), true);
  assert.equal(isArchiveFile("server.tar.zst"), true);
  assert.equal(isArchiveFile("notes.xz"), true);
  assert.equal(isArchiveFile("readme.txt"), false);
  assert.equal(archiveExtensionLabel("CS2Fixes-linux.tar.zst"), "tar.zst");
});
