#!/usr/bin/env node
import { spawn } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { prepareStandaloneAssets } from './with-internal-api-url.mjs';
import { resolveNextRuntime } from './cache-components-runtime.mjs';
const root = resolve(process.env.CC_BUILD_ROOT ?? '/tmp/upkk-cc-build-candidate');
const identity = JSON.parse(readFileSync(resolve(root, 'acceptance/build-input.json'), 'utf8'));
if (identity.variant !== 'candidate' || !identity.testingApi) throw new Error('Navigation tests require a dedicated candidate test build');
const next = resolve(root, '.next/standalone/server.js');
prepareStandaloneAssets(root, next);
const runtime = resolveNextRuntime();
const child = spawn(runtime.executable, [...runtime.prefix, next], { cwd: resolve(root, '.next/standalone'), stdio: 'inherit',
  env: { ...process.env, INTERNAL_API_URL: 'http://127.0.0.1:38261', PUBLIC_APP_URL: 'http://127.0.0.1:31961',
    SESSION_COOKIE_SUFFIX: '', PORT: '31961', HOSTNAME: '127.0.0.1' } });
for (const signal of ['SIGINT', 'SIGTERM']) process.on(signal, () => child.kill(signal));
child.on('exit', code => { process.exitCode = code ?? 1; });
