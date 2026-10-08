import assert from 'node:assert/strict';
import { test } from 'node:test';
import { runtimeCommand, resolveNextRuntime } from './cache-components-runtime.mjs';

const digest = `sha256:${'a'.repeat(64)}`;
test('the default runtime keeps the actual Node executable', () => {
  assert.deepEqual(runtimeCommand({}), { executable: process.execPath, prefix: [], nodePath: process.execPath });
  const identity = JSON.parse(resolveNextRuntime({ ...process.env, CC_RSS_RUNTIME_ROOT: '',
    CC_RSS_RUNTIME_IMAGE_DIGEST: '' }).identity);
  assert.equal(identity.node, process.version);
  assert.equal(identity.platform, process.platform);
  assert.equal(identity.arch, process.arch);
  assert.equal(identity.imageDigest, null);
  assert.match(identity.nodeSha256, /^[a-f0-9]{64}$/);
});
test('the production runtime uses its own loader and libraries without a shell', () => {
  const runtime = runtimeCommand({ root: '/fixture/runtime with spaces', imageDigest: digest,
    platform: 'linux', arch: 'arm64' });
  assert.equal(runtime.executable, '/fixture/runtime with spaces/lib/ld-musl-aarch64.so.1');
  assert.deepEqual(runtime.prefix, ['--library-path',
    '/fixture/runtime with spaces/lib:/fixture/runtime with spaces/usr/lib',
    '/fixture/runtime with spaces/usr/local/bin/node']);
  assert.equal(runtime.imageDigest, digest);
  assert.match(runtimeCommand({ root: '/fixture', imageDigest: digest,
    platform: 'linux', arch: 'x64' }).executable, /ld-musl-x86_64/);
});
test('an unpinned or unsupported production runtime fails before starting Next', () => {
  assert.throws(() => runtimeCommand({ root: '/fixture', platform: 'linux', arch: 'arm64' }), /digest/);
  assert.throws(() => runtimeCommand({ root: '/fixture', imageDigest: 'latest', platform: 'linux', arch: 'arm64' }), /digest/);
  for (const [platform, arch] of [['darwin', 'arm64'], ['linux', 'ia32']]) {
    assert.throws(() => runtimeCommand({ root: '/fixture', imageDigest: digest, platform, arch }), /requires Linux/);
  }
});
