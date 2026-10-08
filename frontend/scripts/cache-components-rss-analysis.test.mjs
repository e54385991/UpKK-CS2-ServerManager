import { test } from 'node:test';
import assert from 'node:assert/strict';
import { compareRssRuns, summarizeRssRun } from './cache-components-rss-analysis.mjs';

function run(bytes = 1000) {
  return { metadata: { mode: 'full', contexts: 30, warmupSeconds: 300,
    measureSeconds: 3600, nextPid: 123, errors: 0, controls: { node: '26', platform: 'darwin', arch: 'arm64', host: 'fixture-host', next: '16.4.0', browserProcesses: 5, contexts: 30, visitSeconds: 15, startIntervalMs: 500, nofile: '65536', tempFilesystemType: 1, memoryLimitBytes: 'host', fixtureHash: 'fixture', workloadHash: 'workload' } },
  samples: Array.from({ length: 781 }, (_, index) => ({ pid: 123, elapsedSeconds: index * 5,
    phase: index < 60 ? 'warmup' : 'measure', rssBytes: bytes })),
  coverage: { warmup: 500, measure: 500 },
  finalConnections: { activeStreams: 0, openedStreams: 2, cancelledStreams: 2 } };
}
test('5% threshold is inclusive and three full runs are required', () => {
  assert.equal(compareRssRuns([run(), run(), run()], [run(1050), run(1050), run(1050)]).passed, true);
  assert.equal(compareRssRuns([run(), run(), run()], [run(1051), run(1051), run(1051)]).passed, false);
  assert.equal(compareRssRuns([run()], [run()]).passed, false);
});
test('smoke, missing hosts, wrong PID and unreleased streams fail closed', () => {
  for (const change of [
    r => { r.metadata.mode = 'smoke'; }, r => { r.coverage.warmup = 499; },
    r => { r.samples[0].pid = 456; }, r => { r.finalConnections.activeStreams = 1; },
    r => { r.metadata.errors = 1; },
    r => { r.samples[0].rssBytes = Infinity; },
    r => { r.samples.splice(100, 5); },
  ]) { const sample = run(); change(sample); assert.equal(summarizeRssRun(sample).passed, false); }
});
test('each round must be stable even when aggregate p95 is below regression limit', () => {
  const growing = run();
  growing.samples.filter(sample => sample.elapsedSeconds >= 3600).forEach(sample => { sample.rssBytes = 1060; });
  assert.equal(summarizeRssRun(growing).stable, false);
  assert.equal(compareRssRuns([run(), run(), run()], [growing, run(), run()]).passed, false);
});

test('mismatched runtime, host, fixture or request sequence cannot pass', () => {
  for (const key of ['node', 'host', 'visitSeconds', 'startIntervalMs', 'nofile', 'tempFilesystemType', 'memoryLimitBytes', 'fixtureHash', 'workloadHash']) {
    const candidate = run(); candidate.metadata.controls[key] = 'different';
    assert.equal(compareRssRuns([run(), run(), run()], [candidate, run(), run()]).passed, false);
  }
});
