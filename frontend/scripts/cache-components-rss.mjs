#!/usr/bin/env node
import { spawn, execFileSync } from 'node:child_process';
import { mkdirSync, readFileSync, writeFileSync, createWriteStream } from 'node:fs';
import { resolve } from 'node:path';
import { once } from 'node:events';
import { hostname } from 'node:os';
import { createHash } from 'node:crypto';
import { setTimeout as delay } from 'node:timers/promises';
import { chromium } from '@playwright/test';
import { prepareStandaloneAssets } from './with-internal-api-url.mjs';
import { RSS_PROTOCOL, summarizeRssRun } from './cache-components-rss-analysis.mjs';
import { CACHE_COMPONENTS_ACTORS, fixtureServerIds, FIXTURE_OPERATION } from '../e2e/helpers/cache-components-surfaces.mjs';

const [buildArg, reportArg, mode = 'full', roundsArg = '3'] = process.argv.slice(2);
if (!buildArg || !reportArg || !['full', 'smoke'].includes(mode)) {
  throw new Error('Usage: node scripts/cache-components-rss.mjs BUILD_FRONTEND REPORT_DIR full|smoke [ROUNDS]');
}
const build = resolve(buildArg);
const report = resolve(reportArg);
const identity = JSON.parse(readFileSync(resolve(build, 'acceptance/build-input.json'), 'utf8'));
if (identity.testingApi) throw new Error('RSS measurements require a production build with the testing API disabled');
if (execFileSync('ps', ['-eo', 'comm='], { encoding: 'utf8' }).split('\n')
  .some(command => command.trim().startsWith('next-build'))) {
  throw new Error('Finish Next production builds before starting the isolated RSS workload');
}
const rounds = mode === 'full' ? Number(roundsArg) : 1;
if (mode === 'full' && rounds !== 3) throw new Error('The acceptance protocol requires exactly three rounds');
const warmupSeconds = mode === 'full' ? RSS_PROTOCOL.warmupSeconds : 10;
const measureSeconds = mode === 'full' ? RSS_PROTOCOL.measureSeconds : 30;
const visitSeconds = 15;
const baseURL = 'http://cache-components.test:31961';
const listenerURL = 'http://127.0.0.1:31961';
const mockURL = 'http://127.0.0.1:38261';
const hash = paths => createHash('sha256').update(Buffer.concat(paths.map(path => readFileSync(resolve(import.meta.dirname, path))))).digest('hex');
const controls = { node: process.version, platform: process.platform, arch: process.arch, host: hostname(),
  next: JSON.parse(readFileSync(resolve(build, 'node_modules/next/package.json'))).version,
  browserProcesses: 5, contexts: 30, visitSeconds,
  fixtureHash: hash(['../e2e/cache-components.mock.mjs', '../e2e/performance.mock.mjs', '../e2e/helpers/cache-components-surfaces.mjs', '../e2e/helpers/cache-components-responses.mjs']),
  workloadHash: hash(['cache-components-rss.mjs']),
};
mkdirSync(report, { recursive: true });

async function ready(url, child) {
  for (let attempt = 0; attempt < 120; attempt++) {
    if (child.exitCode !== null) throw new Error(`Process exited ${child.exitCode} before ${url}`);
    try { if ((await fetch(url)).ok) return; } catch { /* process is starting */ }
    await delay(500);
  }
  throw new Error(`Process did not become ready: ${url}`);
}
async function stop(child) {
  if (!child || child.exitCode !== null || child.signalCode !== null) return;
  child.kill('SIGTERM');
  await Promise.race([once(child, 'exit'), delay(5000)]);
  if (child.exitCode === null && child.signalCode === null) {
    const exited = once(child, 'exit'); child.kill('SIGKILL'); await exited;
  }
}
async function snapshot() {
  return (await fetch(`${mockURL}/__test__/connections`)).json();
}
async function setPhase(phase) {
  await fetch(`${mockURL}/__test__/phase`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ phase }) });
}

async function load(context, actor, index, keepGoing, measured, error, diagnose) {
  let pending = new Set();
  const page = await context.newPage();
  page.setDefaultTimeout(15000);
  page.on('pageerror', error);
  page.on('request', request => pending.add(request.url()));
  page.on('requestfinished', request => pending.delete(request.url()));
  page.on('requestfailed', request => pending.delete(request.url()));
  const ids = fixtureServerIds(actor);
  const tenantWorkers = Array.from({ length: 30 }, (_, worker) => worker)
    .filter(worker => CACHE_COMPONENTS_ACTORS[worker % 4].at(-1) === actor.at(-1));
  let step = tenantWorkers.indexOf(index);
  await delay(index * 250);
  try {
    while (keepGoing()) {
      const iterationStarted = performance.now();
      const id = ids[step % ids.length];
      pending = new Set();
      try {
        await page.goto(`/servers/${id}/monitoring`, { waitUntil: 'commit' });
        await page.getByTestId('monitoring-form').waitFor();
        // Check meaningful UI; persistent streams are cancelled explicitly.
        await page.evaluate(async ({ id, operation }) => {
          for (const url of [`/ops-stream/servers/${id}/operations/${operation}`, '/ai-stream/runs/fixture-run']) {
            const controller = new AbortController();
            const response = await fetch(url, { signal: controller.signal });
            if (!response.ok) throw new Error(`Stream ${url}: ${response.status}`);
            const reader = response.body.getReader();
            await reader.read();
            controller.abort();
            await reader.cancel().catch(() => undefined);
          }
        }, { id, operation: FIXTURE_OPERATION });
        if (measured() && step % 5 === 0) {
          await page.locator('a[data-workspace-category="files"]').click({ noWaitAfter: true });
          await page.goto('about:blank', { waitUntil: 'commit' });
          await page.goto(`/servers/${id}/monitoring`, { waitUntil: 'commit' });
          await page.getByTestId('monitoring-form').waitFor();
        }
        // Dispose the old router/document before the next visit so a late
        // client navigation cannot abort the following document navigation.
        await page.goto('about:blank', { waitUntil: 'commit' });
      } catch (failure) {
        if (keepGoing()) {
          error(JSON.stringify({ failure: String(failure), actor, index, id,
            url: page.url(), pending: [...pending].slice(0, 12) }));
          await diagnose(actor, index, id);
        }
      }
      step += tenantWorkers.length;
      await delay(Math.max(0, visitSeconds * 1000 - (performance.now() - iterationStarted)));
    }
  } finally {
    // Reuse one tab per context. Each document navigation cancels the previous
    // document's readers, while avoiding renderer churn from creating hundreds
    // of tabs during the one-hour workload.
    await page.close().catch(() => undefined);
  }
}

const results = [];
for (let round = 1; round <= rounds; round++) {
  let mockProcess;
  let nextProcess;
  let browsers = [];
  let running = true;
  let errors = 0;
  const roundStop = new AbortController();
  const failures = [];
  const recordError = error => {
    errors += 1;
    if (failures.length < 100) failures.push(String(error));
    writeFileSync(resolve(report, `round-${round}-errors.json`), JSON.stringify({ errors, failures }, null, 2));
    if (mode === 'full') { running = false; roundStop.abort(); }
  };
  const interrupt = () => { recordError('RSS round interrupted by process signal'); running = false; roundStop.abort(); };
  process.once('SIGINT', interrupt); process.once('SIGTERM', interrupt);
  async function waitForLoad(seconds) {
    try { await delay(seconds * 1000, undefined, { signal: roundStop.signal }); }
    catch (error) { if (!roundStop.signal.aborted) throw error; }
  }
  const samples = [];
  const diagnostics = [];
  async function diagnose(actor, index, id) {
    const started = performance.now();
    let http;
    try {
      const response = await fetch(`${listenerURL}/servers/${id}/monitoring`, {
        headers: { cookie: `upkk_access_token=fixture-${actor}; locale=${Math.floor(index / 4) % 2 ? 'zh-CN' : 'en-US'}` },
        signal: AbortSignal.timeout(5000),
      });
      http = { status: response.status, bytes: (await response.arrayBuffer()).byteLength };
    } catch (failure) { http = { failure: String(failure) }; }
    diagnostics.push({ actor, index, id, http, milliseconds: performance.now() - started,
      processes: execFileSync('ps', ['-eo', 'pid,ppid,rss,pcpu,stat,comm'], { encoding: 'utf8' }) });
    writeFileSync(resolve(report, `round-${round}-diagnostics.json`), JSON.stringify(diagnostics, null, 2));
  }
  const log = createWriteStream(resolve(report, `round-${round}.log`));
  const raw = createWriteStream(resolve(report, `round-${round}.ndjson`));
  let timer;
  try {
    const env = { ...process.env, INTERNAL_API_URL: mockURL, PUBLIC_APP_URL: baseURL,
      SESSION_COOKIE_SUFFIX: '', PORT: '31961', HOSTNAME: '127.0.0.1', CC_MOCK_PORT: '38261', NEXT_TELEMETRY_DISABLED: '1' };
    mockProcess = spawn(process.execPath, [resolve(import.meta.dirname, '../e2e/cache-components.mock.mjs')], { env });
    mockProcess.stdout.pipe(log, { end: false }); mockProcess.stderr.pipe(log, { end: false });
    await ready(`${mockURL}/health`, mockProcess);
    const serverPath = resolve(build, '.next/standalone/server.js');
    prepareStandaloneAssets(build, serverPath);
    nextProcess = spawn(process.execPath, [serverPath], { cwd: resolve(build, '.next/standalone'), env });
    nextProcess.stdout.pipe(log, { end: false }); nextProcess.stderr.pipe(log, { end: false });
    await ready(`${listenerURL}/login`, nextProcess);
    browsers = await Promise.all(Array.from({ length: controls.browserProcesses }, () => chromium.launch({ headless: true,
      args: ['--host-resolver-rules=MAP cache-components.test 127.0.0.1', '--no-proxy-server'] })));
    const contexts = await Promise.all(Array.from({ length: 30 }, (_, index) => browsers[index % browsers.length].newContext({
      baseURL, viewport: { width: Math.floor(index / 8) % 2 ? 1440 : 390, height: 844 },
      locale: Math.floor(index / 4) % 2 ? 'zh-CN' : 'en-US',
    })));
    await Promise.all(contexts.map(async (context, index) => {
      await context.addCookies([
        { name: 'upkk_access_token', value: `fixture-${CACHE_COMPONENTS_ACTORS[index % 4]}`, url: baseURL, httpOnly: true },
        { name: 'locale', value: Math.floor(index / 4) % 2 ? 'zh-CN' : 'en-US', url: baseURL },
      ]);
    }));
    const started = performance.now();
    let phase = 'warmup';
    async function sample() {
      try {
        const rssBytes = Number(execFileSync('ps', ['-o', 'rss=', '-p', String(nextProcess.pid)], { encoding: 'utf8' }).trim()) * 1024;
        const row = { at: new Date().toISOString(), pid: nextProcess.pid, rssBytes,
          elapsedSeconds: (performance.now() - started) / 1000, phase, connections: await snapshot(), errors };
        samples.push(row); raw.write(`${JSON.stringify(row)}\n`);
      } catch (failure) { recordError(failure); }
    }
    await sample();
    let nextSample = started + 5000;
    let sampling = true;
    const scheduleSample = () => {
      timer = setTimeout(async () => {
        await sample();
        nextSample += 5000;
        if (sampling) scheduleSample();
      }, Math.max(0, nextSample - performance.now()));
    };
    scheduleSample();
    const jobs = contexts.map((context, index) => load(context, CACHE_COMPONENTS_ACTORS[index % 4], index,
      () => running, () => phase === 'measure', recordError, diagnose).catch(recordError));
    console.log(`${identity.variant} round ${round}: pid ${nextProcess.pid}, 30 contexts, warmup ${warmupSeconds}s + measure ${measureSeconds}s`);
    await waitForLoad(warmupSeconds);
    if (!roundStop.signal.aborted) {
      phase = 'measure'; await setPhase(phase);
      await waitForLoad(measureSeconds);
    }
    sampling = false; clearTimeout(timer);
    await sample();
    running = false;
    await Promise.all(contexts.map(context => context.close()));
    await Promise.all(jobs);
    await delay(2000);
    const finalConnections = await snapshot();
    const metadata = { ...identity, buildId: readFileSync(resolve(build, '.next/BUILD_ID'), 'utf8').trim(),
      mode, round, controls, node: process.version, contexts: 30, warmupSeconds, measureSeconds,
      nextPid: nextProcess.pid, errors, failures };
    const result = { metadata, samples, finalConnections, coverage: finalConnections.coverage };
    results.push(result);
    writeFileSync(resolve(report, `round-${round}.json`), JSON.stringify(result, null, 2));
    const summary = summarizeRssRun(result);
    console.log(JSON.stringify(summary));
    if (mode === 'full' && !summary.passed) break;
  } finally {
    running = false; clearTimeout(timer);
    process.off('SIGINT', interrupt); process.off('SIGTERM', interrupt);
    await Promise.all(browsers.map(browser => browser.close()));
    await stop(nextProcess); await stop(mockProcess);
    raw.end(); log.end();
  }
}
writeFileSync(resolve(report, 'runs.json'), JSON.stringify(results, null, 2));
writeFileSync(resolve(report, 'summary.json'), JSON.stringify({ mode, rounds: results.map(summarizeRssRun) }, null, 2));
// A smoke run is infrastructure validation and can never produce an acceptance verdict.
if (mode === 'full' && results.some(run => !summarizeRssRun(run).passed)) process.exitCode = 1;
