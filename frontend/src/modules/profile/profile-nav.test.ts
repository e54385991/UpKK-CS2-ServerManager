import assert from "node:assert/strict";
import test from "node:test";
import { PROFILE_SECTIONS, profileSectionFromHash } from "./profile-nav.ts";

test("profileSectionFromHash maps category hashes and credential field aliases", () => {
  assert.equal(profileSectionFromHash(""), "account");
  assert.equal(profileSectionFromHash("#profile-account"), "account");
  assert.equal(profileSectionFromHash("profile-credentials"), "credentials");
  assert.equal(profileSectionFromHash("#profile-github-token"), "credentials");
  assert.equal(profileSectionFromHash("#profile-steam-key"), "credentials");
  assert.equal(profileSectionFromHash("#profile-security"), "security");
  assert.equal(profileSectionFromHash("#profile-backups"), "backups");
  assert.equal(profileSectionFromHash("#profile-ai"), "ai");
  assert.equal(profileSectionFromHash("#profile-operations"), "operations");
  assert.equal(profileSectionFromHash("#unknown"), "account");
});

test("every profile category has a unique hash id", () => {
  const ids = PROFILE_SECTIONS.map((section) => section.id);
  assert.equal(new Set(ids).size, ids.length);
});
