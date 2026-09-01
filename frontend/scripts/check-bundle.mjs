import { gzipSync } from "node:zlib";
import { readFile, readdir } from "node:fs/promises";
import { join, relative } from "node:path";

const projectRoot = new URL("..", import.meta.url).pathname;
const nextRoot = join(projectRoot, ".next");
const routeRoot = join(nextRoot, "server", "app");
const INITIAL_ROUTE_BUDGET = 250 * 1024;
const INITIAL_CHUNK_BUDGET = 150 * 1024;

async function walk(directory) {
  const entries = await readdir(directory, { withFileTypes: true });
  const files = [];
  for (const entry of entries) {
    const path = join(directory, entry.name);
    if (entry.isDirectory()) files.push(...(await walk(path)));
    else if (entry.name === "build-manifest.json") files.push(path);
  }
  return files;
}

const manifests = await walk(routeRoot);
const checked = new Set();
const violations = [];

for (const manifestPath of manifests) {
  const manifest = JSON.parse(await readFile(manifestPath, "utf8"));
  const route = relative(routeRoot, manifestPath).replace(/\/build-manifest\.json$/, "");
  const chunks = [...new Set([...(manifest.rootMainFiles ?? []), ...(manifest.polyfillFiles ?? [])])];
  let routeGzip = 0;
  for (const chunk of chunks) {
    const chunkPath = join(nextRoot, chunk);
    if (checked.has(chunkPath)) {
      routeGzip += gzipSync(await readFile(chunkPath)).length;
      continue;
    }
    const size = gzipSync(await readFile(chunkPath)).length;
    checked.add(chunkPath);
    routeGzip += size;
    if (size > INITIAL_CHUNK_BUDGET) {
      violations.push(`${chunk} is ${size} bytes gzip (limit ${INITIAL_CHUNK_BUDGET})`);
    }
  }
  if (routeGzip > INITIAL_ROUTE_BUDGET) {
    violations.push(`${route} is ${routeGzip} bytes gzip (limit ${INITIAL_ROUTE_BUDGET})`);
  }
}

if (violations.length > 0) {
  console.error("Next.js bundle budget exceeded:");
  for (const violation of violations) console.error(`  - ${violation}`);
  process.exitCode = 1;
} else {
  console.log(`Next.js bundle budget passed for ${manifests.length} routes.`);
}
