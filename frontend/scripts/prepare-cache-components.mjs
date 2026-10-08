#!/usr/bin/env node
import { cpSync, existsSync, mkdirSync, readFileSync, readdirSync, writeFileSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { basename, resolve } from 'node:path';
import { createHash } from 'node:crypto';

const [sourceArg, destinationArg, variant = 'candidate', purpose = 'navigation'] = process.argv.slice(2);
if (!sourceArg || !destinationArg || !['baseline', 'candidate'].includes(variant) || !['navigation', 'memory'].includes(purpose)) {
  throw new Error('Usage: node scripts/prepare-cache-components.mjs SOURCE_FRONTEND NEW_DESTINATION baseline|candidate');
}
const source = resolve(sourceArg);
const destination = resolve(destinationArg);
if (existsSync(destination) || destination.startsWith(`${source}/`)) {
  throw new Error('Destination must be a new isolated directory outside the source frontend');
}
const excluded = new Set(['node_modules', '.next', '.next-cache-components', '.cache-components', 'test-results',
  'playwright-report', 'blob-report', '.auth', 'next-env.d.ts', 'dump.rdb', '.DS_Store']);
cpSync(source, destination, { recursive: true, filter: path =>
  !excluded.has(basename(path)) && !basename(path).startsWith('.env') && !path.endsWith('.tsbuildinfo') });
cpSync(resolve(import.meta.dirname, '../node_modules'), resolve(destination, 'node_modules'), { recursive: true });
mkdirSync(resolve(destination, 'acceptance'), { recursive: true });
if (variant === 'candidate' && purpose === 'navigation') {
  const hub = resolve(destination, 'src/app/[locale]/cc-navigation');
  mkdirSync(hub, { recursive: true });
  const { CACHE_COMPONENTS_SURFACES } = await import('../e2e/helpers/cache-components-surfaces.mjs');
  writeFileSync(resolve(hub, 'page.tsx'), `import { Link } from "@/i18n/navigation";
const paths = ${JSON.stringify(CACHE_COMPONENTS_SURFACES)} as const;
export default function TestNavigationHub() {
  return <main data-testid="test-navigation">{paths.map(href => <Link key={href} href={href} data-testid={"navigate:" + href}>{href}</Link>)}</main>;
}\n`);
}
const configPath = resolve(destination, 'next.config.ts');
const content = readFileSync(configPath, 'utf8');
if (process.env.CC_REVERT_OVERVIEW === '1') {
  if (variant !== 'candidate' || purpose !== 'navigation') throw new Error('Differential control is navigation-test-only');
  const path = resolve(destination, 'src/app/[locale]/(console)/overview/page.tsx');
  let page = readFileSync(path, 'utf8');
  page = page.replace('export default async function OverviewPage() {', 'async function BlockingOverviewPage() {\n  await getSession();');
  page += '\nexport default function OverviewPage() { return <Suspense fallback={<OverviewStatsSkeleton />}><BlockingOverviewPage /></Suspense>; }\n';
  writeFileSync(path, page);
}
const inputHash = createHash('sha256');
const git = spawnSync('git', ['rev-parse', 'HEAD'], { cwd: source, encoding: 'utf8' });
const diff = spawnSync('git', ['diff', '--', 'frontend'], { cwd: source, encoding: 'utf8' });
inputHash.update(git.stdout).update(diff.stdout).update(readFileSync(resolve(destination, 'package-lock.json')));
function hashSource(directory) {
  for (const entry of readdirSync(directory, { withFileTypes: true }).sort((a, b) => a.name.localeCompare(b.name))) {
    if (excluded.has(entry.name) || entry.name.startsWith('.env') || entry.name.endsWith('.tsbuildinfo')) continue;
    const path = resolve(directory, entry.name);
    if (entry.isDirectory()) hashSource(path);
    else if (entry.isFile()) inputHash.update(path.slice(destination.length)).update(readFileSync(path));
  }
}
hashSource(destination);
const env = { ...process.env, INTERNAL_API_URL: 'http://127.0.0.1:38261',
  PUBLIC_APP_URL: 'http://127.0.0.1:31961', SESSION_COOKIE_SUFFIX: '',
  CC_CANDIDATE_BUILD: variant === 'candidate' ? '1' : '0', CC_TEST_BUILD: '1',
  CC_EXPOSE_TEST_API: purpose === 'navigation' ? '1' : '0',
  NEXT_TELEMETRY_DISABLED: '1' };
// The original baseline has no candidate config; it stays unchanged.
const identity = {
  source, destination, variant, git: git.stdout.trim(), inputSha256: inputHash.digest('hex'),
  node: process.version, platform: process.platform, arch: process.arch, configSha256:
    createHash('sha256').update(content).digest('hex'), purpose, revertedOverview: process.env.CC_REVERT_OVERVIEW === '1', testingApi: false,
  createdAt: new Date().toISOString(),
};
writeFileSync(resolve(destination, 'acceptance/build-input.json'), JSON.stringify(identity, null, 2));
const build = spawnSync(process.execPath, ['node_modules/next/dist/bin/next', 'build'], {
  cwd: destination, env, stdio: 'inherit',
});
if (build.status !== 0) process.exit(build.status ?? 1);
const actual = JSON.parse(readFileSync(resolve(destination, '.next/required-server-files.json'), 'utf8')).config;
identity.testingApi = Boolean(actual.experimental.exposeTestingApiInProductionBuild);
identity.cacheComponents = Boolean(actual.cacheComponents);
identity.partialPrefetching = Boolean(actual.partialPrefetching);
writeFileSync(resolve(destination, 'acceptance/build-input.json'), JSON.stringify(identity, null, 2));
if (variant === 'candidate' && (!identity.cacheComponents || !identity.partialPrefetching ||
  identity.testingApi !== (purpose === 'navigation'))) throw new Error('Built candidate flags differ from the requested isolated test contract');
if (variant === 'candidate' && purpose === 'memory') {
  const { measureBundles, bundleViolations } = await import('./bundle-budget.mjs');
  const bundles = await measureBundles(resolve(destination, '.next'));
  const violations = bundleViolations(bundles);
  writeFileSync(resolve(destination, 'acceptance/bundle-budget.json'), JSON.stringify({
    maxBytes: Math.max(...bundles.routes.map(route => route.gzip)), violations,
  }, null, 2));
  if (violations.length) throw new Error(`Candidate production bundle budget exceeded: ${violations.join('; ')}`);
}
console.log(`Isolated ${variant} build: ${destination}`);
