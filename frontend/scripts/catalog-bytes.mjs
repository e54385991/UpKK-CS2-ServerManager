import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

export const LOGIN_NAMESPACES = ["site", "feedback", "login"];
export const OVERVIEW_NAMESPACES = ["site", "feedback", "nav", "shell", "overview"];

const here = dirname(fileURLToPath(import.meta.url));
const messagesRoot = join(here, "../src/i18n/messages");

export function compactBytes(value) {
  return Buffer.byteLength(JSON.stringify(value), "utf8");
}

export function pickNamespaces(catalog, names) {
  return Object.fromEntries(names.filter((name) => name in catalog).map((name) => [name, catalog[name]]));
}

export function loadCatalog(locale) {
  const main = JSON.parse(readFileSync(join(messagesRoot, `${locale}.json`), "utf8"));
  const monitor = JSON.parse(readFileSync(join(messagesRoot, "monitor", `${locale}.json`), "utf8"));
  return { ...main, settings: { ...(main.settings ?? {}), monitor } };
}

export function catalogByteReport() {
  const locales = {};
  for (const locale of ["en-US", "zh-CN"]) {
    const catalog = loadCatalog(locale);
    locales[locale] = {
      full: compactBytes(catalog),
      login_subset: compactBytes(pickNamespaces(catalog, LOGIN_NAMESPACES)),
      overview_subset: compactBytes(pickNamespaces(catalog, OVERVIEW_NAMESPACES)),
    };
  }
  return {
    note: "Compact UTF-8 JSON of the server catalog, not HTML/RSC/gzip transfer.",
    locales,
    client_estimate: {
      login: locales["en-US"].full,
      overview: locales["en-US"].full,
      login_zh: locales["zh-CN"].full,
      overview_zh: locales["zh-CN"].full,
    },
  };
}

if (import.meta.url === `file://${process.argv[1]}`) {
  process.stdout.write(`${JSON.stringify(catalogByteReport(), null, 2)}\n`);
}
