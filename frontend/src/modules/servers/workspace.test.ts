import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import type { AbstractIntlMessages } from "next-intl";
import { pickMessages } from "../../i18n/pick-messages.ts";
import {
  WORKSPACE_FRAME_NAMESPACES,
  workspacePageNamespaces,
} from "./workspace-messages.ts";
import {
  SERVER_WORKSPACE_CATEGORIES,
  WORKSPACE_NAV_PREFETCH,
  workspaceNavPrefetchOnIntent,
} from "./workspace.ts";

test("workspace nav does not prefetch in-viewport SSH pages", () => {
  assert.equal(WORKSPACE_NAV_PREFETCH, false);
});

test("only game config and host config prefetch after hover or focus", () => {
  assert.equal(SERVER_WORKSPACE_CATEGORIES.length, 19);
  for (const category of SERVER_WORKSPACE_CATEGORIES) {
    assert.equal(
      workspaceNavPrefetchOnIntent(category),
      category === "config" || category === "host-config",
      category,
    );
  }
});

for (const locale of ["en-US", "zh-CN"] as const) {
  const catalog = JSON.parse(
    readFileSync(new URL(`../../i18n/messages/${locale}.json`, import.meta.url), "utf8"),
  ) as AbstractIntlMessages;

  test(`${locale} workspace catalogs resolve every category without modifying shared messages`, () => {
    const original = JSON.stringify(catalog);
    for (const category of SERVER_WORKSPACE_CATEGORIES) {
      assert.doesNotThrow(() => pickMessages(catalog, workspacePageNamespaces(category)));
    }
    assert.equal(JSON.stringify(catalog), original);
  });

  test(`${locale} monitoring keeps its copy within 40 KiB and excludes unrelated workspace dictionaries`, () => {
    const frame = pickMessages(catalog, WORKSPACE_FRAME_NAMESPACES);
    const monitoring = pickMessages(catalog, workspacePageNamespaces("monitoring"));
    assert.ok(Buffer.byteLength(JSON.stringify(frame)) < 40 * 1024);
    assert.ok(Buffer.byteLength(JSON.stringify(monitoring)) < 40 * 1024);
    assert.deepEqual(monitoring.serverMonitoring, catalog.serverMonitoring);
    assert.ok(!("files" in monitoring));
    assert.ok(!("maps" in monitoring));
    assert.ok(!("profile" in monitoring));
    assert.ok(!("serverMonitoring" in frame));
  });
}
