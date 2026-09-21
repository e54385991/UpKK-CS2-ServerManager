import assert from "node:assert/strict";
import test from "node:test";
import {
  base64urlToBuffer,
  bufferToBase64url,
  hostnameIsIp,
  isPasskeyErrorCode,
} from "./webauthn.ts";

test("base64url round-trips binary challenge bytes", () => {
  const bytes = new Uint8Array([1, 2, 250, 255, 0, 61]);
  const encoded = bufferToBase64url(bytes);
  assert.equal(encoded.includes("+"), false);
  assert.equal(encoded.includes("/"), false);
  assert.equal(encoded.includes("="), false);
  assert.deepEqual(new Uint8Array(base64urlToBuffer(encoded)), bytes);
});

test("hostnameIsIp rejects passkey use on literal addresses", () => {
  assert.equal(hostnameIsIp("192.168.1.8"), true);
  assert.equal(hostnameIsIp("::1"), true);
  assert.equal(hostnameIsIp("[::1]"), true);
  assert.equal(hostnameIsIp("localhost"), false);
  assert.equal(hostnameIsIp("panel.example.com"), false);
});

test("isPasskeyErrorCode recognizes stable API error codes", () => {
  assert.equal(isPasskeyErrorCode("passkey_ip_origin"), true);
  assert.equal(isPasskeyErrorCode("nope"), false);
});
