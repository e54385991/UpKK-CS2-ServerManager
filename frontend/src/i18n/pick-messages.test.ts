import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { pickMessages } from "./pick-messages.ts";
import {
  CONSOLE_NAMESPACES,
  LOGIN_CLIENT_NAMESPACES,
} from "./namespaces.ts";

type Catalog = Record<string, unknown>;

function readEnglish(): Catalog {
  const url = new URL("./messages/en-US.json", import.meta.url);
  const monitorUrl = new URL("./messages/monitor/en-US.json", import.meta.url);
  const main = JSON.parse(readFileSync(url, "utf8")) as Catalog;
  const monitor = JSON.parse(readFileSync(monitorUrl, "utf8")) as Catalog;
  const settings = (main.settings ?? {}) as Catalog;
  return { ...main, settings: { ...settings, monitor } };
}

test("pickMessages copies nested namespaces without sibling keys", () => {
  const catalog = {
    feedback: { ok: "OK" },
    plugins: {
      title: "Plugins",
      aiImport: { title: "Import", usage: { tokens: "Tokens" } },
      github: { error: "GitHub" },
    },
  };
  const picked = pickMessages(catalog, ["feedback", "plugins.aiImport"]);
  assert.deepEqual(picked, {
    feedback: { ok: "OK" },
    plugins: { aiImport: { title: "Import", usage: { tokens: "Tokens" } } },
  });
});

test("pickMessages merges two nested paths under the same parent", () => {
  const catalog = {
    plugins: { aiImport: { a: 1 }, github: { b: 2 }, other: { c: 3 } },
  };
  const picked = pickMessages(catalog, ["plugins.aiImport", "plugins.github"]);
  assert.deepEqual(picked, {
    plugins: { aiImport: { a: 1 }, github: { b: 2 } },
  });
});

test("pickMessages throws when a namespace is missing", () => {
  assert.throws(
    () => pickMessages({ feedback: { ok: "OK" } }, ["login"]),
    /Missing message namespace: login/,
  );
});

test("login and overview client catalogs stay under half of the full dictionary", () => {
  const catalog = readEnglish();
  const full = Buffer.byteLength(JSON.stringify(catalog), "utf8");
  const login = Buffer.byteLength(
    JSON.stringify(pickMessages(catalog, LOGIN_CLIENT_NAMESPACES)),
    "utf8",
  );
  const overview = Buffer.byteLength(
    JSON.stringify(pickMessages(catalog, [...CONSOLE_NAMESPACES])),
    "utf8",
  );
  assert.ok(login * 2 < full, `login ${login} is not 50% below ${full}`);
  assert.ok(overview * 2 < full, `overview ${overview} is not 50% below ${full}`);
});
