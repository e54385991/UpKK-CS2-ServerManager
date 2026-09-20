import assert from "node:assert/strict";
import test from "node:test";
import {
  addServerAfterSetupHref,
  canonicalGameDirectory,
  hostsMatch,
  isHostReadyToAdd,
  isInvalidGameDirectory,
  normalizeHost,
  parseHostDirectoryConflict,
  pickInitializedHost,
  setupWizardHref,
} from "./initialized-hosts.ts";

test("normalizeHost trims and lowercases", () => {
  assert.equal(normalizeHost("  192.168.50.141  "), "192.168.50.141");
  assert.equal(normalizeHost("Box.LAN"), "box.lan");
});

test("hostsMatch ignores case and surrounding space", () => {
  assert.equal(hostsMatch("192.168.50.141", "192.168.50.141 "), true);
  assert.equal(hostsMatch("Box.LAN", "box.lan"), true);
  assert.equal(hostsMatch("10.0.0.1", "10.0.0.2"), false);
  assert.equal(hostsMatch(" ", " "), false);
});

test("isHostReadyToAdd accepts saved or marked hosts", () => {
  const saved = [{ host: "192.168.50.141" }];
  assert.equal(isHostReadyToAdd("192.168.50.141", saved), true);
  assert.equal(isHostReadyToAdd("10.0.0.8", saved), false);
  assert.equal(isHostReadyToAdd("10.0.0.8", saved, "10.0.0.8"), true);
});

test("pickInitializedHost prefers key, then marked host, then the only item", () => {
  const listed = [
    { key: "init:1:a", host: "10.0.0.1" },
    { key: "init:1:b", host: "192.168.50.141" },
  ];
  assert.deepEqual(
    pickInitializedHost(listed, { preferredKey: "init:1:b" }),
    listed[1],
  );
  assert.deepEqual(
    pickInitializedHost(listed, { markedHost: "192.168.50.141" }),
    listed[1],
  );
  assert.equal(pickInitializedHost(listed), undefined);
  assert.deepEqual(
    pickInitializedHost([listed[0]]),
    listed[0],
  );
});

test("setup and add-server hrefs carry the init gate", () => {
  assert.equal(
    setupWizardHref({
      name: "lan",
      host: "192.168.50.141",
      sshPort: 22,
      sshUser: "root",
    }),
    "/servers/new?tab=setup&requireInit=1&name=lan&host=192.168.50.141&sshPort=22&sshUser=root",
  );
  assert.equal(
    addServerAfterSetupHref({
      host: "192.168.50.141",
      initializedServerId: "init:1:abc",
      sshUser: "cs2server",
    }),
    "/servers/new?initialized=1&host=192.168.50.141&from=init%3A1%3Aabc&sshUser=cs2server",
  );
});

test("canonicalGameDirectory collapses dots and trailing slashes", () => {
  assert.equal(canonicalGameDirectory("/home/cs2server/cs2/"), "/home/cs2server/cs2");
  assert.equal(canonicalGameDirectory("/home/cs2server/cs2/../cs2-2"), "/home/cs2server/cs2-2");
});

test("isInvalidGameDirectory rejects relative and root paths", () => {
  assert.equal(isInvalidGameDirectory("/home/cs2server/cs2"), false);
  assert.equal(isInvalidGameDirectory("home/cs2server/cs2"), true);
  assert.equal(isInvalidGameDirectory("/"), true);
  assert.equal(isInvalidGameDirectory(" / "), true);
});

test("parseHostDirectoryConflict reads a structured 409 body", () => {
  const conflict = parseHostDirectoryConflict(409, "fallback", {
    code: "host_directory_exists",
    message: "occupied",
    existing_server_id: 12,
    existing_server_name: "lan",
    host: "192.168.50.143",
    game_directory: "/home/cs2server/cs2",
  });
  assert.deepEqual(conflict, {
    code: "host_directory_exists",
    message: "occupied",
    existingServerId: 12,
    existingServerName: "lan",
    host: "192.168.50.143",
    gameDirectory: "/home/cs2server/cs2",
  });
});

test("parseHostDirectoryConflict ignores other errors", () => {
  assert.equal(
    parseHostDirectoryConflict(400, "bad", { code: "host_directory_exists", existing_server_id: 1 }),
    undefined,
  );
  assert.equal(parseHostDirectoryConflict(409, "busy", "lock held"), undefined);
  assert.equal(
    parseHostDirectoryConflict(409, "busy", { code: "other", existing_server_id: 1 }),
    undefined,
  );
});
