import assert from "node:assert/strict";
import test from "node:test";
import {
  addServerAfterSetupHref,
  hostsMatch,
  isHostReadyToAdd,
  normalizeHost,
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
