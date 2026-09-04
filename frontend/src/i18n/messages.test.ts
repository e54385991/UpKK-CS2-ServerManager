import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

type Catalog = Record<string, string | Catalog>;

function readCatalog(locale: "en-US" | "zh-CN"): Catalog {
  const url = new URL(`./messages/${locale}.json`, import.meta.url);
  return JSON.parse(readFileSync(url, "utf8")) as Catalog;
}

function flatten(catalog: Catalog, prefix = ""): Map<string, string> {
  const messages = new Map<string, string>();
  for (const [key, value] of Object.entries(catalog)) {
    const path = prefix ? `${prefix}.${key}` : key;
    if (typeof value === "string") messages.set(path, value);
    else {
      for (const [nestedKey, nestedValue] of flatten(value, path)) {
        messages.set(nestedKey, nestedValue);
      }
    }
  }
  return messages;
}

function variables(message: string): string[] {
  return [...message.matchAll(/\{\s*([A-Za-z_][\w]*)\s*(?:,|\})/g)]
    .map((match) => match[1] ?? "")
    .filter(Boolean)
    .sort();
}

test("English and Simplified Chinese catalogs have identical keys and ICU variables", () => {
  const english = flatten(readCatalog("en-US"));
  const chinese = flatten(readCatalog("zh-CN"));

  assert.deepEqual([...chinese.keys()].sort(), [...english.keys()].sort());
  for (const [key, englishMessage] of english) {
    const chineseMessage = chinese.get(key);
    assert.equal(typeof chineseMessage, "string", `${key} is missing from zh-CN`);
    assert.notEqual(englishMessage.trim(), "", `${key} is empty in en-US`);
    assert.notEqual(chineseMessage?.trim(), "", `${key} is empty in zh-CN`);
    assert.deepEqual(
      variables(chineseMessage ?? ""),
      variables(englishMessage),
      `${key} uses different ICU variables`,
    );
  }
});

test("the English catalog contains no Chinese copy", () => {
  for (const [key, message] of flatten(readCatalog("en-US"))) {
    assert.doesNotMatch(message, /[\u3400-\u9fff]/u, `${key} contains Chinese copy`);
  }
});
