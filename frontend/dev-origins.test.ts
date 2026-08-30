import assert from "node:assert/strict";
import test from "node:test";
import { lanDevOrigins } from "./dev-origins.ts";

test("allows the INTERNAL_API_URL host and ignores bind PUBLIC_APP_URL", () => {
  const previousApi = process.env.INTERNAL_API_URL;
  const previousPublic = process.env.PUBLIC_APP_URL;
  process.env.INTERNAL_API_URL = "http://192.168.50.245:8000";
  process.env.PUBLIC_APP_URL = "http://0.0.0.0:3005";
  try {
    const hosts = lanDevOrigins();
    assert.ok(hosts.includes("192.168.50.245"));
    assert.ok(!hosts.includes("0.0.0.0"));
  } finally {
    if (previousApi === undefined) delete process.env.INTERNAL_API_URL;
    else process.env.INTERNAL_API_URL = previousApi;
    if (previousPublic === undefined) delete process.env.PUBLIC_APP_URL;
    else process.env.PUBLIC_APP_URL = previousPublic;
  }
});
