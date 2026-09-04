import assert from "node:assert/strict";
import test from "node:test";
import {
  localeFromAcceptLanguage,
  resolveLocale,
} from "./config.ts";

test("browser Chinese variants use Simplified Chinese", () => {
  for (const language of ["zh", "zh-CN", "zh-TW", "zh-HK", "zh-Hans", "zh-Hant"]) {
    assert.equal(localeFromAcceptLanguage(language), "zh-CN");
  }
});

test("non-Chinese, missing, and malformed browser languages use English", () => {
  for (const language of [undefined, null, "", "en-US", "fr-FR", "de-DE", "*", "q="]) {
    assert.equal(localeFromAcceptLanguage(language), "en-US");
  }
});

test("Accept-Language honors quality and original order", () => {
  assert.equal(localeFromAcceptLanguage("en-US;q=0.7, zh-CN;q=0.9"), "zh-CN");
  assert.equal(localeFromAcceptLanguage("en-US, zh-CN;q=0.9"), "en-US");
  assert.equal(localeFromAcceptLanguage("zh-CN;q=0, en-US;q=0.8"), "en-US");
  assert.equal(localeFromAcceptLanguage("fr-FR, zh-CN;q=0.9"), "en-US");
});

test("a valid locale cookie overrides browser detection", () => {
  assert.equal(resolveLocale("en-US", "zh-CN"), "en-US");
  assert.equal(resolveLocale("zh-CN", "en-US"), "zh-CN");
  assert.equal(resolveLocale("invalid", "zh-HK"), "zh-CN");
});
