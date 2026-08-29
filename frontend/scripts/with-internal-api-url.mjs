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
import { cpSync, existsSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const DEFAULT_ORIGIN = "http://127.0.0.1:8000";
const here = dirname(fileURLToPath(import.meta.url));

export function hostGatewayFromRouteTable(text) {
  for (const line of text.split(/\r?\n/).slice(1)) {
    const cols = line.trim().split(/\s+/);
    if (cols[1] !== "00000000" || !cols[2] || cols[2] === "00000000") continue;
    if (!/^[0-9a-fA-F]{8}$/.test(cols[2])) continue;
    const bytes = [];
    for (let i = 6; i >= 0; i -= 2) {
      bytes.push(Number.parseInt(cols[2].slice(i, i + 2), 16));
    }
    return bytes.join(".");
  }
  return undefined;
}

export function isNonLoopbackIpHost(hostname) {
  if (!/^\d{1,3}(\.\d{1,3}){3}$/.test(hostname)) return false;
  const parts = hostname.split(".").map(Number);
  if (parts.some((n) => Number.isNaN(n) || n > 255)) return false;
  return parts[0] !== 127;
}

export function apiOriginCandidates(configured, gateway) {
  const origin = configured.replace(/\/$/, "");
  const unique = [];
  const push = (item) => {
    if (item && !unique.includes(item)) unique.push(item);
  };
  try {
    const url = new URL(origin);
    const port = url.port || (url.protocol === "https:" ? "443" : "80");
    const dockerHost = [];
    if (gateway && gateway !== url.hostname) {
      dockerHost.push(`${url.protocol}//${gateway}:${port}`);
    }
    if (url.hostname !== "host.docker.internal") {
      dockerHost.push(`${url.protocol}//host.docker.internal:${port}`);
    }
    // A container using the host LAN IP (1Panel split runtimes) hairpins and
    // hangs /api/captcha. Try the Docker host first; keep the LAN IP last.
    if (isNonLoopbackIpHost(url.hostname)) {
      for (const item of dockerHost) push(item);
      push(origin);
    } else {
      push(origin);
      for (const item of dockerHost) push(item);
    }
  } catch {
    push(origin);
  }
  return unique;
}

async function originIsHealthy(origin, fetchImpl, timeoutMs = 800) {
  const ac = new AbortController();
  const timer = setTimeout(() => ac.abort(), timeoutMs);
  try {
    const response = await fetchImpl(`${origin}/health`, {
      signal: ac.signal,
      cache: "no-store",
    });
    return response.ok;
  } catch {
    return false;
  } finally {
    clearTimeout(timer);
  }
}

export async function pickReachableApiOrigin(
  configured,
  fetchImpl = fetch,
  gateway = existsSync("/proc/net/route")
    ? hostGatewayFromRouteTable(readFileSync("/proc/net/route", "utf8"))
    : undefined,
) {
  const candidates = apiOriginCandidates(configured, gateway);
  for (const origin of candidates) {
    if (await originIsHealthy(origin, fetchImpl)) {
      if (origin !== configured) {
        console.info(
          `[frontend] ${configured} is not reachable from this process; using ${origin}`,
        );
      }
      return origin;
    }
  }
  console.warn(
    `[frontend] no candidate answered /health (${candidates.join(", ")}). keeping ${configured}`,
  );
  return configured;
}

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

export function resolveStandaloneServer(appRoot) {
  const candidates = [
    resolve(process.cwd(), "server.js"),
    resolve(appRoot, ".next/standalone/server.js"),
  ];
  return candidates.find((filePath) => existsSync(filePath));
}

export function prepareStandaloneAssets(appRoot, serverPath) {
  const standaloneRoot = dirname(serverPath);
  const copies = [
    [resolve(appRoot, ".next/static"), resolve(standaloneRoot, ".next/static")],
    [resolve(appRoot, "public"), resolve(standaloneRoot, "public")],
  ];
  for (const [from, to] of copies) {
    if (existsSync(from)) {
      cpSync(from, to, { recursive: true, force: true });
    }
  }
}

function applyManifest(filePath, origin) {
  if (!existsSync(filePath)) return false;
  const data = JSON.parse(readFileSync(filePath, "utf8"));
  if (rewriteApiDestinations(data, origin)) {
    writeFileSync(filePath, JSON.stringify(data));
  }
  return true;
}

function followChild(child) {
  child.on("exit", (code, signal) => {
    if (signal) {
      process.kill(process.pid, signal);
      return;
    }
    process.exit(code ?? 1);
  });
}

function startServer(appRoot) {
  const serverPath = resolveStandaloneServer(appRoot);
  if (serverPath) {
    prepareStandaloneAssets(appRoot, serverPath);
    const standaloneRoot = dirname(serverPath);
    const child = spawn(process.execPath, [serverPath], {
      stdio: "inherit",
      cwd: standaloneRoot,
      env: {
        ...process.env,
        PORT: process.env.PORT || "3000",
        HOSTNAME: process.env.HOSTNAME || "0.0.0.0",
      },
    });
    followChild(child);
    return;
  }

  console.warn(
    "[frontend] standalone server.js not found; falling back to next start",
  );
  const nextBin = resolve(appRoot, "node_modules/next/dist/bin/next");
  const child = spawn(process.execPath, [nextBin, "start", "--port", process.env.PORT || "3000"], {
    stdio: "inherit",
    env: process.env,
    cwd: appRoot,
  });
  followChild(child);
}

async function main() {
  const appRoot = resolveAppRoot();
  for (const name of [".env", ".env.local", ".env.production", ".env.production.local"]) {
    loadEnvFile(resolve(appRoot, name));
  }

  const configured = (process.env.INTERNAL_API_URL || DEFAULT_ORIGIN).replace(/\/$/, "");
  const origin = await pickReachableApiOrigin(configured);
  process.env.INTERNAL_API_URL = origin;
  const manifests = [
    resolve(appRoot, ".next/routes-manifest.json"),
    resolve(appRoot, ".next/standalone/.next/routes-manifest.json"),
    resolve(process.cwd(), ".next/routes-manifest.json"),
  ];
  for (const filePath of new Set(manifests)) {
    applyManifest(filePath, origin);
  }
  console.info(`[frontend] proxying /api /health /static to ${origin}`);
  startServer(appRoot);
}

if (import.meta.url === pathToFileURL(process.argv[1] ?? "").href) {
  main().catch((error) => {
    console.error(error);
    process.exit(1);
  });
}
