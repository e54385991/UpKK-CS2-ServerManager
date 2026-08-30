import assert from "node:assert/strict";
import test from "node:test";
import {
  configuredPublicAppUrl,
  hostFromOriginUrl,
  publicAppUrlFromHeaders,
} from "./public-app-url.ts";

test("hostFromOriginUrl ignores bind addresses and empty values", () => {
  assert.equal(hostFromOriginUrl(undefined), undefined);
  assert.equal(hostFromOriginUrl("http://0.0.0.0:3005"), undefined);
  assert.equal(hostFromOriginUrl("http://192.168.50.245:8000"), "192.168.50.245");
});

test("publicAppUrlFromHeaders uses the request Host and port", () => {
  const previous = process.env.PUBLIC_APP_URL;
  delete process.env.PUBLIC_APP_URL;
  try {
    const headers = new Headers({ host: "192.168.50.245:3000" });
    assert.equal(publicAppUrlFromHeaders(headers), "http://192.168.50.245:3000");
  } finally {
    if (previous === undefined) delete process.env.PUBLIC_APP_URL;
    else process.env.PUBLIC_APP_URL = previous;
  }
});

test("publicAppUrlFromHeaders prefers X-Forwarded-Host and proto", () => {
  const previous = process.env.PUBLIC_APP_URL;
  delete process.env.PUBLIC_APP_URL;
  try {
    const headers = new Headers({
      host: "127.0.0.1:3000",
      "x-forwarded-host": "panel.example:443",
      "x-forwarded-proto": "https",
    });
    assert.equal(publicAppUrlFromHeaders(headers), "https://panel.example");
  } finally {
    if (previous === undefined) delete process.env.PUBLIC_APP_URL;
    else process.env.PUBLIC_APP_URL = previous;
  }
});

test("configured PUBLIC_APP_URL=0.0.0.0 is ignored so Host wins", () => {
  const previous = process.env.PUBLIC_APP_URL;
  process.env.PUBLIC_APP_URL = "http://0.0.0.0:3005";
  try {
    assert.equal(configuredPublicAppUrl(), null);
    const headers = new Headers({ host: "192.168.50.245:3000" });
    assert.equal(publicAppUrlFromHeaders(headers), "http://192.168.50.245:3000");
  } finally {
    if (previous === undefined) delete process.env.PUBLIC_APP_URL;
    else process.env.PUBLIC_APP_URL = previous;
  }
});
