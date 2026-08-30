import assert from "node:assert/strict";
import test from "node:test";
import { cleanupScanStreamUrl, cleanupSystemStreamUrl } from "./stream.ts";

test("cleanup stream urls stay on the cookie-to-bearer proxy", () => {
  assert.equal(cleanupScanStreamUrl(7), "/cleanup-stream/servers/7/scan");
  assert.equal(cleanupSystemStreamUrl(12), "/cleanup-stream/servers/12/system");
});
