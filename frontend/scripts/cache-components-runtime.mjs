import { createHash } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

/** Start the actual Next Node process; the browser harness keeps its own Node. */
export function runtimeCommand({ root, imageDigest, platform = process.platform, arch = process.arch } = {}) {
  if (!root) return { executable: process.execPath, prefix: [], nodePath: process.execPath };
  if (platform !== 'linux' || !['arm64', 'x64'].includes(arch)) {
    throw new Error('The pinned Alpine runtime requires Linux arm64 or x64');
  }
  if (!/^sha256:[a-f0-9]{64}$/.test(imageDigest ?? '')) {
    throw new Error('A pinned image digest is required for the isolated Next runtime');
  }
  const directory = resolve(root);
  const loader = resolve(directory, 'lib', `ld-musl-${arch === 'arm64' ? 'aarch64' : 'x86_64'}.so.1`);
  const nodePath = resolve(directory, 'usr/local/bin/node');
  return { executable: loader, prefix: ['--library-path', `${directory}/lib:${directory}/usr/lib`, nodePath],
    nodePath, imageDigest };
}

export function resolveNextRuntime(env = process.env) {
  const command = runtimeCommand({ root: env.CC_RSS_RUNTIME_ROOT, imageDigest: env.CC_RSS_RUNTIME_IMAGE_DIGEST });
  const info = JSON.parse(execFileSync(command.executable, [...command.prefix, '--input-type=module', '-e',
    "console.log(JSON.stringify({node:process.version,platform:process.platform,arch:process.arch,libc:process.report.getReport().header.glibcVersionRuntime??(process.platform==='linux'?'musl':process.platform)}))"],
  { encoding: 'utf8', timeout: 15000, env }));
  if (info.node !== process.version || info.platform !== process.platform || info.arch !== process.arch) {
    throw new Error('The Next runtime and browser harness must use the same Node version, platform and architecture');
  }
  if (command.imageDigest && info.libc !== 'musl') throw new Error('The pinned Alpine runtime did not use musl');
  const sha256 = path => createHash('sha256').update(readFileSync(path)).digest('hex');
  const identity = JSON.stringify({ ...info, imageDigest: command.imageDigest ?? null,
    nodeSha256: sha256(command.nodePath), loaderSha256: command.imageDigest ? sha256(command.executable) : null });
  return { ...command, identity };
}
