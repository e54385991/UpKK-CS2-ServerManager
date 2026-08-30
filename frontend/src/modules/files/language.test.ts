import assert from "node:assert/strict";
import test from "node:test";
import { editorLanguageId } from "./language.ts";

test("editorLanguageId maps common server-manager files", () => {
  assert.equal(editorLanguageId("server.cfg"), "cfg");
  assert.equal(editorLanguageId("plugin.jsonc"), "json");
  assert.equal(editorLanguageId("config.yml"), "yaml");
  assert.equal(editorLanguageId("gamemode.ini"), "properties");
  assert.equal(editorLanguageId("console.log"), "cfg");
  assert.equal(editorLanguageId("gameinfo.vdf"), "cfg");
  assert.equal(editorLanguageId(".env"), "properties");
  assert.equal(editorLanguageId("readme.md"), "md");
  assert.equal(editorLanguageId("notes.txt"), "txt");
  assert.equal(editorLanguageId("addons"), "");
});
