import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

/** Keep in sync with frontend/src/i18n/namespaces.ts client payloads. */
export const LOGIN_NAMESPACES = ["feedback", "login"];
export const OVERVIEW_NAMESPACES = [
  "feedback",
  "site",
  "nav",
  "shell",
  "serverDetail",
  "plugins.aiImport",
];

const here = dirname(fileURLToPath(import.meta.url));
const messagesRoot = join(here, "../src/i18n/messages");

export function compactBytes(value) {
  return Buffer.byteLength(JSON.stringify(value), "utf8");
}

function isPlainObject(value) {
  return value != null && typeof value === "object" && !Array.isArray(value);
}

function assignPath(target, source, parts, namespace) {
  const head = parts[0];
  if (head == null || !(head in source)) {
    throw new Error(`Missing message namespace: ${namespace}`);
  }
  if (parts.length === 1) {
    target[head] = source[head];
    return;
  }
  const value = source[head];
  if (!isPlainObject(value)) {
    throw new Error(`Message namespace is not nested: ${namespace}`);
  }
  const next = isPlainObject(target[head]) ? target[head] : {};
  target[head] = next;
  assignPath(next, value, parts.slice(1), namespace);
}

export function pickNamespaces(catalog, names) {
  const result = {};
  for (const name of names) {
    assignPath(result, catalog, name.split("."), name);
  }
  return result;
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
      login: locales["en-US"].login_subset,
      overview: locales["en-US"].overview_subset,
      login_zh: locales["zh-CN"].login_subset,
      overview_zh: locales["zh-CN"].overview_subset,
    },
  };
}

if (import.meta.url === `file://${process.argv[1]}`) {
  process.stdout.write(`${JSON.stringify(catalogByteReport(), null, 2)}\n`);
}
