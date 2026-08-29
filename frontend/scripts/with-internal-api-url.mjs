#!/usr/bin/env node
/**
 * Apply runtime INTERNAL_API_URL to Next's baked rewrite destinations, then
 * start the server. `next build` serializes rewrites into
 * `.next/routes-manifest.json`; without this, a standalone image always
 * proxies to the build-time default (http://127.0.0.1:8000) even when
 * Docker/compose sets INTERNAL_API_URL=http://app:8000.
 *
 * `next dev` does not need this: next.config.ts is evaluated on startup.
 */
import { spawn } from "node:child_process";
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const DEFAULT_ORIGIN = "http://127.0.0.1:8000";
const here = dirname(fileURLToPath(import.meta.url));

export function rewriteApiDestinations(manifest, origin) {
  const groups = manifest?.rewrites;
  if (!groups) return false;
  const lists = Array.isArray(groups)
    ? [groups]
    : [groups.beforeFiles, groups.afterFiles, groups.fallback].filter(Array.isArray);
  let changed = false;
  for (const list of lists) {
    for (const rule of list) {
      if (typeof rule.destination !== "string") continue;
      let dest;
      try {
        dest = new URL(rule.destination);
      } catch {
        continue;
      }
      const path = dest.pathname;
      const isProxy =
        path === "/health" ||
        path === "/api" ||
        path.startsWith("/api/") ||
        path.startsWith("/static/");
      if (!isProxy) continue;
      const next = `${origin}${path}${dest.search}`;
      if (rule.destination !== next) {
        rule.destination = next;
        changed = true;
      }
    }
  }
  return changed;
}

function loadEnvFile(filePath) {
  if (!existsSync(filePath)) return;
  for (const raw of readFileSync(filePath, "utf8").split(/\r?\n/)) {
    const line = raw.trim();
    if (!line || line.startsWith("#")) continue;
    const eq = line.indexOf("=");
    if (eq <= 0) continue;
    const key = line.slice(0, eq).trim();
    if (!key || process.env[key] !== undefined) continue;
    let value = line.slice(eq + 1).trim();
    if (
      (value.startsWith('"') && value.endsWith('"')) ||
      (value.startsWith("'") && value.endsWith("'"))
    ) {
      value = value.slice(1, -1);
    }
    process.env[key] = value;
  }
}

function resolveAppRoot() {
  if (existsSync(resolve(here, "server.js"))) return here;
  const parent = resolve(here, "..");
  if (existsSync(resolve(parent, "package.json"))) return parent;
  return process.cwd();
}

function applyManifest(filePath, origin) {
  if (!existsSync(filePath)) return false;
  const data = JSON.parse(readFileSync(filePath, "utf8"));
  if (rewriteApiDestinations(data, origin)) {
    writeFileSync(filePath, JSON.stringify(data));
  }
  return true;
}

function startServer(appRoot) {
  const standalone = existsSync(resolve(process.cwd(), "server.js"));
  if (standalone) {
    const child = spawn(process.execPath, [resolve(process.cwd(), "server.js")], {
      stdio: "inherit",
      env: process.env,
    });
    child.on("exit", (code, signal) => {
      if (signal) {
        process.kill(process.pid, signal);
        return;
      }
      process.exit(code ?? 1);
    });
    return;
  }

  const nextBin = resolve(appRoot, "node_modules/next/dist/bin/next");
  const child = spawn(process.execPath, [nextBin, "start", "--port", process.env.PORT || "3000"], {
    stdio: "inherit",
    env: process.env,
    cwd: appRoot,
  });
  child.on("exit", (code, signal) => {
    if (signal) {
      process.kill(process.pid, signal);
      return;
    }
    process.exit(code ?? 1);
  });
}

function main() {
  const appRoot = resolveAppRoot();
  for (const name of [".env", ".env.local", ".env.production", ".env.production.local"]) {
    loadEnvFile(resolve(appRoot, name));
  }

  const origin = (process.env.INTERNAL_API_URL || DEFAULT_ORIGIN).replace(/\/$/, "");
  const manifests = [
    resolve(appRoot, ".next/routes-manifest.json"),
    resolve(process.cwd(), ".next/routes-manifest.json"),
  ];
  for (const filePath of new Set(manifests)) {
    applyManifest(filePath, origin);
  }
  console.info(`[frontend] proxying /api /health /static to ${origin}`);
  startServer(appRoot);
}

if (import.meta.url === pathToFileURL(process.argv[1] ?? "").href) {
  main();
}
