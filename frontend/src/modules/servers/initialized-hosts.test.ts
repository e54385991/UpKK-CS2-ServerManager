import assert from "node:assert/strict";
import test from "node:test";
import {
  addServerAfterSetupHref,
  hostsMatch,
  isHostReadyToAdd,
  normalizeHost,
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
    }),
    "/servers/new?initialized=1&host=192.168.50.141&from=init%3A1%3Aabc",
  );
});
