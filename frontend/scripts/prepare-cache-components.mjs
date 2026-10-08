#!/usr/bin/env node
import { cpSync, existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
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
const excluded = new Set(['node_modules', '.next', '.cache-components', 'test-results',
  'playwright-report', 'blob-report', '.auth', 'next-env.d.ts']);
cpSync(source, destination, { recursive: true, filter: path =>
  !excluded.has(basename(path)) && !basename(path).startsWith('.env') && !path.endsWith('.tsbuildinfo') });
cpSync(resolve(import.meta.dirname, '../node_modules'), resolve(destination, 'node_modules'), { recursive: true });
mkdirSync(resolve(destination, 'acceptance'), { recursive: true });
const configPath = resolve(destination, 'next.config.ts');
const content = readFileSync(configPath, 'utf8');
const inputHash = createHash('sha256');
const git = spawnSync('git', ['rev-parse', 'HEAD'], { cwd: source, encoding: 'utf8' });
const diff = spawnSync('git', ['diff', '--', 'frontend'], { cwd: source, encoding: 'utf8' });
inputHash.update(git.stdout).update(diff.stdout).update(readFileSync(resolve(destination, 'package-lock.json')));
const env = { ...process.env, INTERNAL_API_URL: 'http://127.0.0.1:38261',
  PUBLIC_APP_URL: 'http://127.0.0.1:31961', SESSION_COOKIE_SUFFIX: '',
  CC_CANDIDATE_BUILD: variant === 'candidate' ? '1' : '0', CC_TEST_BUILD: '1',
  CC_EXPOSE_TEST_API: purpose === 'navigation' ? '1' : '0',
  NEXT_TELEMETRY_DISABLED: '1' };
// The original baseline has no candidate config; it stays unchanged.
const identity = {
  source, destination, variant, git: git.stdout.trim(), inputSha256: inputHash.digest('hex'),
  node: process.version, platform: process.platform, arch: process.arch, configSha256:
    createHash('sha256').update(content).digest('hex'), purpose, testingApi: false,
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
console.log(`Isolated ${variant} build: ${destination}`);
