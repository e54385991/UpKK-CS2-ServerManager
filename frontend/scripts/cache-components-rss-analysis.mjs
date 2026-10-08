export const RSS_PROTOCOL = Object.freeze({ contexts: 30, serverIds: 500,
  warmupSeconds: 300, measureSeconds: 3600, sampleSeconds: 5, rounds: 3, limit: 1.05 });

export function quantile(values, ratio) {
  if (!values.length) throw new Error('RSS samples are missing');
  const sorted = [...values].sort((a, b) => a - b);
  return sorted[Math.max(0, Math.ceil(sorted.length * ratio) - 1)];
}

export function summarizeRssRun(run) {
  const { samples, metadata, finalConnections, coverage } = run;
  const measured = samples.filter(sample => sample.phase === 'measure');
  const first = measured.filter(sample => sample.elapsedSeconds < 600);
  const last = measured.filter(sample => sample.elapsedSeconds >= 3600);
  const complete = metadata.mode === 'full' && metadata.contexts === 30 &&
    metadata.warmupSeconds === 300 && metadata.measureSeconds === 3600 &&
    measured.length >= 720 && samples.at(-1)?.elapsedSeconds >= 3900 &&
    coverage.warmup === 500 && coverage.measure === 500 &&
    measured[0]?.elapsedSeconds <= 310 &&
    samples.every((sample, index) => sample.pid === metadata.nextPid && Number.isFinite(sample.rssBytes) && sample.rssBytes > 0 &&
      (index === 0 || sample.elapsedSeconds - samples[index - 1].elapsedSeconds <= 10));
  const growth = first.length && last.length
    ? quantile(last.map(sample => sample.rssBytes), 0.5) /
      quantile(first.map(sample => sample.rssBytes), 0.5) : null;
  const cancelled = finalConnections.activeStreams === 0 &&
    finalConnections.openedStreams > 0 &&
    finalConnections.openedStreams === finalConnections.cancelledStreams;
  return { complete, p95Bytes: measured.length ? quantile(measured.map(sample => sample.rssBytes), 0.95) : null,
    peakBytes: samples.length ? Math.max(...samples.map(sample => sample.rssBytes)) : null,
    growth, stable: growth !== null && growth <= RSS_PROTOCOL.limit,
    cancelled, errors: metadata.errors, passed: complete && growth !== null &&
      growth <= RSS_PROTOCOL.limit && cancelled && metadata.errors === 0 };
}

export function compareRssRuns(baseline, candidate) {
  const before = baseline.map(summarizeRssRun);
  const after = candidate.map(summarizeRssRun);
  const reference = baseline[0]?.metadata.controls;
  const controlKeys = ['node', 'platform', 'arch', 'host', 'next', 'browserProcesses', 'contexts',
    'visitSeconds', 'startIntervalMs', 'nofile', 'tempFilesystemType', 'memoryLimitBytes', 'fixtureHash', 'workloadHash'];
  const matched = Boolean(reference) && [...baseline, ...candidate].every(run =>
    controlKeys.every(key => reference[key] !== undefined && reference[key] === run.metadata.controls?.[key]));
  const complete = matched && before.length === 3 && after.length === 3 &&
    [...before, ...after].every(run => run.complete);
  const medianP95 = runs => runs.length && runs.every(run => run.p95Bytes !== null)
    ? quantile(runs.map(run => run.p95Bytes), 0.5) : null;
  const baselineP95 = medianP95(before);
  const candidateP95 = medianP95(after);
  const ratio = baselineP95 && candidateP95 ? candidateP95 / baselineP95 : null;
  return { protocol: RSS_PROTOCOL, matched, complete, baseline: before, candidate: after,
    baselineMedianP95Bytes: baselineP95, candidateMedianP95Bytes: candidateP95,
    ratio, passed: complete && ratio !== null && ratio <= RSS_PROTOCOL.limit &&
      [...before, ...after].every(run => run.passed) };
}
