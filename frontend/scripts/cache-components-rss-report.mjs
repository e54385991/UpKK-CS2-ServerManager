#!/usr/bin/env node
import { readFileSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { compareRssRuns } from './cache-components-rss-analysis.mjs';
const [baseline, candidate, output] = process.argv.slice(2);
if (!baseline || !candidate || !output) throw new Error('Usage: node scripts/cache-components-rss-report.mjs BASELINE_REPORT CANDIDATE_REPORT OUTPUT_JSON');
const read = path => JSON.parse(readFileSync(resolve(path, 'runs.json'), 'utf8'));
const verdict = compareRssRuns(read(baseline), read(candidate));
writeFileSync(resolve(output), JSON.stringify(verdict, null, 2));
console.log(JSON.stringify(verdict));
if (!verdict.passed) process.exitCode = 1;
