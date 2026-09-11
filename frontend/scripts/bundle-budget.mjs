import { gzipSync } from "node:zlib";
import { readFile, readdir } from "node:fs/promises";
import { dirname, join, relative, resolve } from "node:path";
import { runInNewContext } from "node:vm";

export const INITIAL_ROUTE_BUDGET = 250 * 1024;
export const INITIAL_CHUNK_BUDGET = 150 * 1024;

async function manifestsIn(directory) {
  const files = [];
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    const path = join(directory, entry.name);
    if (entry.isDirectory()) files.push(...await manifestsIn(path));
    else if (entry.name === "build-manifest.json" && dirname(path).endsWith("/page")) files.push(path);
  }
  return files;
}

function strings(value, label) {
  if (!Array.isArray(value) || !value.every(item => typeof item === "string")) {
    throw new Error(`Invalid Next.js manifest: ${label}`);
  }
  return value;
}

/** Next 16/Turbopack keeps route entry chunks in the RSC manifest, not rootMainFiles. */
export async function measureBundles(nextRoot) {
  const routeRoot = join(nextRoot, "server", "app");
  const manifests = await manifestsIn(routeRoot);
  if (!manifests.length) throw new Error("No Next.js page build manifests found. Run a production build first.");
  const chunkSizes = new Map();
  const routes = [];
  for (const manifestPath of manifests.sort()) {
    const route = relative(routeRoot, dirname(manifestPath));
    const build = JSON.parse(await readFile(manifestPath, "utf8"));
    const sandbox = {};
    const clientPath = `${dirname(manifestPath)}_client-reference-manifest.js`;
    runInNewContext(await readFile(clientPath, "utf8"), sandbox, { timeout: 1000, filename: clientPath });
    const clients = Object.values(sandbox.__RSC_MANIFEST ?? {});
    if (clients.length !== 1 || !clients[0].entryJSFiles || typeof clients[0].entryJSFiles !== "object") {
      throw new Error(`Invalid Next.js client reference manifest: ${route}`);
    }
    const chunks = [...new Set([
      ...strings(build.rootMainFiles, `${route}: rootMainFiles`),
      ...strings(build.polyfillFiles, `${route}: polyfillFiles`),
      ...Object.values(clients[0].entryJSFiles).flatMap(value => strings(value, `${route}: entryJSFiles`)),
    ])];
    let gzip = 0;
    for (const chunk of chunks) {
      if (!chunk.startsWith("static/") || relative(resolve(nextRoot), resolve(nextRoot, chunk)).startsWith("..")) {
        throw new Error(`Invalid chunk path: ${chunk}`);
      }
      if (!chunkSizes.has(chunk)) chunkSizes.set(chunk, gzipSync(await readFile(join(nextRoot, chunk))).length);
      gzip += chunkSizes.get(chunk);
    }
    routes.push({ route, chunks, gzip });
  }
  return { routes, chunkSizes };
}

export function bundleViolations({ routes, chunkSizes }, routeBudget = INITIAL_ROUTE_BUDGET, chunkBudget = INITIAL_CHUNK_BUDGET) {
  return [
    ...[...chunkSizes].filter(([, size]) => size > chunkBudget)
      .map(([chunk, size]) => `${chunk} is ${size} bytes gzip (limit ${chunkBudget})`),
    ...routes.filter(({ gzip }) => gzip > routeBudget)
      .map(({ route, gzip }) => `${route} is ${gzip} bytes gzip (limit ${routeBudget})`),
  ];
}
