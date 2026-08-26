import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext, Script } from 'node:vm';
import test from 'node:test';

const read = (path) => readFileSync(new URL(`../../${path}`, import.meta.url), 'utf8');

test('page scripts compile as standalone browser assets', () => {
  for (const path of [
    'static/js/plugin-market.js',
    'static/js/profile.js',
    'static/js/servers.js',
  ]) {
    assert.doesNotThrow(() => new Script(read(path), { filename: path }));
  }
});

test('plugin market initialization and pure formatters remain available', () => {
  const listeners = new Map();
  const context = {
    console,
    document: {
      addEventListener: (name, callback) => listeners.set(name, callback),
    },
  };
  runInNewContext(read('static/js/plugin-market.js'), context);

  assert.equal(typeof listeners.get('DOMContentLoaded'), 'function');
  assert.equal(context.formatBytes(0), '0 Bytes');
  assert.equal(context.formatBytes(1024), '1 KB');
});

test('server list Alpine factory evaluates without starting network work', () => {
  const context = {
    console,
    document: { getElementById: () => null },
  };
  runInNewContext(read('static/js/servers.js'), context);

  assert.equal(typeof context.serverManager, 'function');
  const state = context.serverManager();
  assert.equal(typeof state.init, 'function');
  assert.equal(state.maxAggressivePolls, 20);
  assert.equal(state.refreshInterval, null);
});

test('templates reference extracted assets and contain no large inline blocks', () => {
  const contracts = new Map([
    ['templates/plugin_market.html', ['js/plugin-market.js', 'css/plugin-market.css']],
    ['templates/profile.html', ['js/profile.js']],
    ['templates/servers.html', ['js/servers.js', 'css/servers.css']],
  ]);
  for (const [path, assets] of contracts) {
    const template = read(path);
    assert.doesNotMatch(template, /<script>/);
    assert.doesNotMatch(template, /<style>/);
    for (const asset of assets) assert.match(template, new RegExp(asset.replace('.', '\\.')));
  }
});
