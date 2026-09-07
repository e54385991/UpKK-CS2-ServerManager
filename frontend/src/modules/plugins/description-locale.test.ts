import assert from "node:assert/strict";
import test from "node:test";
import { localizedPluginDescription } from "./types.ts";

const plugin = {
  description: "Original summary",
  descriptionI18n: {
    original: "Original summary",
    zh_cn: "中文摘要",
    en_us: null,
  },
};

test("market descriptions follow the active locale when a translation exists", () => {
  assert.equal(localizedPluginDescription(plugin, "zh-CN"), "中文摘要");
  assert.equal(localizedPluginDescription(plugin, "en-US"), "Original summary");
});

test("market descriptions fall back to the canonical description", () => {
  assert.equal(
    localizedPluginDescription(
      { description: "Legacy description", descriptionI18n: null },
      "zh-CN",
    ),
    "Legacy description",
  );
});
