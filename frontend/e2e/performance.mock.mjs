// Loopback-only fixture: never contacts a real database, provider, Redis or SSH host.
import { createServer } from 'node:http';
const port = Number(process.env.PERF_MOCK_PORT ?? 38131);
let requests = [];
let rules = {};
let savedContent = 'hostname "fixture"\n';
const waiters = new Map();
const stamp = '2026-09-09T00:00:00Z';
const server = id => ({ id, name: `fixture-server-${id}`, host: `fixture-${id}.invalid`, ssh_user: 'fixture', ssh_port: 22, game_port: 27015, status: 'running', default_map: 'de_dust2', max_players: 32, game_directory: '/srv/cs2', game_mode: '0', game_type: '0', server_name: 'fixture', session_manager: 'tmux', enable_panel_monitoring: true, monitor_interval_seconds: 60, auto_restart_on_crash: false, enable_a2s_monitoring: true, a2s_failure_threshold: 3, a2s_check_interval_seconds: 30, enable_auto_update: false, tv_enable: false, is_ssh_down: false, has_sudo_password: false, created_at: stamp, updated_at: stamp, last_deployed: stamp });
const plugin = { id: 1, title: 'Fixture Plugin', description: 'Fixture installation documentation', description_i18n: null, author: 'fixture', version: '1.0', category: 'utility', framework: 'counterstrikesharp', is_recommended: false, github_url: 'https://github.com/fixture/plugin', download_count: 0, install_count: 0, created_at: stamp, dependencies: [], ai_metadata: null };
const inbox = { items: [], failed_items: [], market_import_items: [], active_count: 0, running_count: 0, failed_count: 0, failed_retention_days: 7 };
const app = createServer(async (req, res) => {
  const url = new URL(req.url, `http://127.0.0.1:${port}`);
  const path = url.pathname;
  const json = (value, status = 200) => {
    if (res.destroyed) return;
    res.writeHead(status, { 'content-type': 'application/json' });
    res.end(JSON.stringify(value));
  };
  let text = ''; for await (const part of req) text += part;
  const input = text ? JSON.parse(text) : {};
  if (path === '/__test__/reset') {
    for (const callbacks of waiters.values()) for (const release of callbacks) release();
    waiters.clear(); requests = []; rules = input.rules ?? {}; plugin.title = 'Fixture Plugin'; savedContent = 'hostname "fixture"\n';
    return json({ ok: true });
  }
  if (path === '/__test__/release') {
    for (const release of waiters.get(input.path) ?? []) release();
    waiters.delete(input.path); delete rules[input.path]; return json({ ok: true });
  }
  if (path === '/__test__/state') return json({ requests, savedContent });
  if (path === '/health') return json({ status: 'ok', version: 'fixture' });
  const actor = String(req.headers.authorization ?? '');
  const record = { path, query: url.search, method: req.method, actor, started: Date.now(), ended: null, input };
  requests.push(record);
  const rule = rules[path];
  if (rule?.gate) await new Promise(resolve => {
    const list = waiters.get(path) ?? []; list.push(resolve); waiters.set(path, list);
    res.on('close', resolve);
  });
  if (rule?.delay) await new Promise(resolve => setTimeout(resolve, rule.delay));
  record.ended = Date.now();
  if (res.destroyed) return;
  if (rule?.status) return json({ detail: 'Fixture failure' }, rule.status);
  if (path === '/api/v1/auth/registration-config') return json({ registration_enabled: false, captcha_enabled: false });
  if (path === '/api/v1/auth/me') return actor.includes('invalid') ? json({ detail: 'Expired' }, 401) : json({ id: actor.includes('member') ? 2 : 1, username: actor.includes('member') ? 'fixture-member' : 'fixture-admin', is_admin: !actor.includes('member'), is_active: true, email: null });
  if (path === '/api/v1/operations/inbox/events') {
    res.writeHead(200, { 'content-type': 'text/event-stream' });
    res.write(`event: inbox\ndata: ${JSON.stringify(inbox)}\n\n`);
    const timer = setInterval(() => res.write(': ping\n\n'), 1000);
    res.on('close', () => clearInterval(timer)); return;
  }
  if (path === '/api/v1/operations/inbox') return json(inbox);
  if (path === '/api/v1/ssh-pool') return json({ connections: 0, in_use: 0, idle: 0, leases: 0 });
  if (path === '/api/v1/plugins/market/ai-imports') return json([]);
  if (path === '/api/v1/servers') return json([server(1), server(2)]);
  if (/^\/api\/v1\/servers\/\d+$/.test(path)) {
    const id = Number(path.split('/').at(-1));
    if (id === 404) return json({ detail: 'Not found' }, 404);
    if (id === 2 && actor.includes('member')) return json({ detail: 'Forbidden' }, 403);
    return json(server(id));
  }
  if (path.endsWith('/operations/current')) return json({ operation: null });
  if (path.endsWith('/operations/lock')) return json({ lock_active: false, server_status: 'running' });
  if (path.endsWith('/operations/logs')) return json([]);
  if (path.endsWith('/quick-commands')) return json({ items: [] });
  if (path.endsWith('/disk-space')) return json({ server_id: 1, cached: false });
  if (path.endsWith('/startup-command')) return json({ startup_command: './cs2 -dedicated', cs2_command: './cs2 -dedicated' });
  if (path === '/api/v1/plugins/market') return json({ items: [plugin], total: 1, limit: 20, offset: 0 });
  if (path === '/api/v1/plugins/market/1') {
    if (req.method === 'PATCH') Object.assign(plugin, input);
    return json(plugin);
  }
  if (path.endsWith('/dependency-options') || path.endsWith('/categories')) return json({ items: [] });
  if (/\/servers\/\d+\/plugins$/.test(path)) return json([]);
  if (path === '/api/v1/plugins/github/releases') return json({ releases: [{ tag_name: 'v1.0', name: 'v1.0', prerelease: false, assets: [{ name: 'plugin.zip', size: 100, browser_download_url: 'https://example.invalid/plugin.zip', runtime_compatibility: 'not_applicable' }] }] });
  if (path.endsWith('/plugins/market/1/install')) return json({ operation_id: '00000000-0000-4000-8000-000000000001', server_id: 1, action: 'install_plugin', status: 'queued', started_at: stamp, actor_user_id: 1, stream_url: '/unused', command: 'fixture install' }, 202);
  if (path.endsWith('/preflight')) return json({ server_id: 1, plugin: { id: 1, title: plugin.title }, plan_hash: 'fixture-plan', installed: [], ordered: [], warnings: [], hard_conflicts: [], steps: [], blocked: false, framework: { plugin: 'counterstrikesharp', conflicting: ['swiftly'], installed: ['swiftly'], missing: false, mismatch: true } });
  if (path === '/api/v1/profile') return json({ id: 1, username: 'fixture-admin', has_github_token: true, steamcmd_max_retries: 3 });
  if (path === '/api/v1/plugin-catalog') return json({ format: 'upkk-cs2-plugin-catalog', version: 1, plugins: [], conflicts: [] });
  if (path === '/api/v1/assistant') return json({ provider_ready: true, mode: 'global', model: 'fixture-model', conversations: [{ id: 'conversation-1', title: 'Fixture conversation' }] });
  if (path === '/api/v1/assistant/conversations/conversation-1') return json({ id: 'conversation-1', title: 'Fixture conversation', messages: [] });
  if (path.endsWith('/plugin-diagnostics/recommendation')) return json({ recommended: false, recently_updated: false, restart_count: 0, max_restarts: 3, window_minutes: 10 });
  if (path.endsWith('/a2s')) return json({ query_host: 'fixture-1.invalid', query_port: 27015, success: true, cached: true, live: false, server_info: { server_name: 'fixture', map_name: 'de_dust2', game: 'cs2', player_count: 0, max_players: 32, bot_count: 0, password_protected: false, vac_enabled: true, version: '1', platform: 'linux' }, players: [], timestamp: stamp, last_updated: stamp, response_time_ms: 12 });
  if (path.endsWith('/monitoring-logs')) return json({ items: [{ id: 'log-1', event_type: 'a2s_check', status: 'ok', message: 'Fixture A2S log', created_at: stamp }] });
  if (path.endsWith('/files')) return json({ server_id: 1, root: '/srv/cs2', path: '/srv/cs2', ssh_ok: true, files: ['server.cfg', 'plugin.zip'].map(name => ({ name, path: `/srv/cs2/${name}`, type: 'file', size: 100, modified: Date.parse(stamp) / 1000, permissions: '-rw-r--r--', is_symlink: false })) });
  if (path.endsWith('/files/content')) {
    if (req.method === 'PUT') { savedContent = input.content; return json({ success: true, message: 'Saved' }); }
    return json({ path: '/srv/cs2/server.cfg', content: savedContent });
  }
  if (path.endsWith('/files/rename')) return json({ success: true, message: 'Renamed' });
  if (path.endsWith('/files/archives/inspect')) return json({ folders: ['Plugin'], entries: [], total_entries: 1 });
  return json({ detail: `Unused fixture endpoint: ${path}` }, 404);
});
app.listen(port, '127.0.0.1', () => console.log(`Performance fixture :${port}`));
