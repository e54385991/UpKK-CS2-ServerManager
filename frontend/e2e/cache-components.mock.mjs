// Loopback only. Deterministic tenants and 500 hosts; no real credentials or I/O.
import { createPerformanceFixture } from './performance.mock.mjs';
import { createHash } from 'node:crypto';
import { FIXTURE_STAMP, FIXTURE_OPERATION } from './helpers/cache-components-surfaces.mjs';
import { CACHE_COMPONENTS_RESPONSES as INITIAL_RESPONSES } from './helpers/cache-components-responses.mjs';
let responses = structuredClone(INITIAL_RESPONSES);
const fileContents = new Map();
const port = Number(process.env.CC_MOCK_PORT ?? 38261);
const stats = { activeStreams: 0, openedStreams: 0, cancelledStreams: 0, activeRequests: 0, activeSockets: 0, openedSockets: 0, closedSockets: 0 };
const activeStreamPaths = new Map();
const coverage = { warmup: new Set(), measure: new Set() };
let phase = 'warmup';

function stream(res, frames) {
  const path = res.req.url.split('?')[0];
  activeStreamPaths.set(path, (activeStreamPaths.get(path) ?? 0) + 1);
  stats.openedStreams += 1;
  stats.activeStreams += 1;
  res.writeHead(200, { 'content-type': 'text/event-stream', 'cache-control': 'no-store' });
  for (const [event, data] of frames) res.write(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`);
  const timer = setInterval(() => res.write(': keepalive\n\n'), 1000);
  res.on('close', () => {
    clearInterval(timer);
    stats.activeStreams -= 1;
    stats.cancelledStreams += 1;
    const count = activeStreamPaths.get(path) - 1;
    if (count) activeStreamPaths.set(path, count);
    else activeStreamPaths.delete(path);
  });
}

async function extraHandler({ req, res, path, input, actor, json, server, inbox }) {
  if (path === '/__test__/connections') {
    json({ ...stats, activeStreamPaths: Object.fromEntries(activeStreamPaths),
      coverage: Object.fromEntries(Object.entries(coverage).map(([key, ids]) => [key, ids.size])) });
    return true;
  }
  if (path === '/__test__/phase') {
    phase = input.phase === 'measure' ? 'measure' : 'warmup';
    json({ phase }); return true;
  }
  const match = actor.match(/^Bearer fixture-(admin|member)-(a|b)$/);
  const publicApi = path === '/health' || path.startsWith('/api/captcha/') ||
    path === '/api/v1/auth/registration-config';
  if (path.startsWith('/api/') && !publicApi && !match) {
    json({ detail: 'Invalid or expired fixture session' }, 401); return true;
  }
  if (!match) return false;
  const [, role, tenant] = match;
  const currentUser = { id: (tenant === 'a' ? 0 : 2) + (role === 'admin' ? 1 : 2),
    username: `fixture-${role}-${tenant}`, email: null, is_active: true, is_admin: role === 'admin' };
  const id = Number(path.match(/\/servers\/(\d+)(?:\/|$)/)?.[1]);
  if (id) {
    if (id < 1 || id > 500) { json({ detail: 'Not found' }, 404); return true; }
    if ((id % 2 === 1 ? 'a' : 'b') !== tenant) { json({ detail: 'Forbidden tenant' }, 403); return true; }
    coverage[phase].add(id);
    server(id).name = `fixture-${tenant}-server-${id}`;
  }
  if (path === '/api/v1/auth/me') { json(currentUser); return true; }
  if (path === '/api/v1/assistant/conversations') { json({ id: 'conversation-1', title: 'Fixture conversation', server_id: input.server_id ?? null }); return true; }
  if (path === '/api/v1/assistant/conversations/conversation-1/messages') { json({ id: 'fixture-run', conversation_id: 'conversation-1', status: 'running', error: null }); return true; }
  if (path === '/api/v1/assistant/runs/fixture-run') { json({ id: 'fixture-run', conversation_id: 'conversation-1', status: 'running', error: null, tools: [] }); return true; }
  if (/^\/api\/v1\/servers\/\d+$/.test(path)) {
    if (req.method === 'PATCH') Object.assign(server(id), input);
    json({ ...server(id), restart_required: false }); return true;
  }
  if (path === '/api/v1/servers') {
    json(Array.from({ length: 250 }, (_, index) => {
      const hostId = index * 2 + (tenant === 'a' ? 1 : 2);
      return { ...server(hostId), name: `fixture-${tenant}-server-${hostId}` };
    })); return true;
  }
  if (path === '/api/v1/profile') { json({ ...currentUser, has_github_token: true, steamcmd_max_retries: 3 }); return true; }
  if (path === '/api/v1/announcements' || path === '/api/v1/announcements/admin') {
    json({ items: [], cs2_update_notice: null }); return true;
  }
  if (path === '/api/v1/audit') { json({ items: [], total: 0, limit: 50, offset: 0 }); return true; }
  const schema = {
    '/api/v1/settings': 'SystemSettingsView', '/api/v1/settings/ai': 'AssistantSystemSettingsView',
    '/api/v1/profile/s3': 'ProfileS3View', '/api/v1/profile/ai': 'AssistantUserSettingsView',
    '/api/v1/discord': 'DiscordBotView', '/api/v1/discord/global': 'DiscordBindingView',
    '/api/v1/diagnostics/monitor': 'PanelMonitorView',
  }[path] ?? (
    path.endsWith('/options') ? 'DiscordOptionsView' : path.endsWith('/agent-policy') ? 'AgentPolicyView' : path.endsWith('/maps') ? 'MapsWorkspaceView' : path.endsWith('/game-modes') ? 'GameModeCatalogView' :
      path.endsWith('/s3-backups') ? 'S3BackupListView' : path.endsWith('/discord') ? 'DiscordBindingView' :
      path.endsWith('/plugin-configs/sources') ? 'PluginConfigSourcesView' :
        path.endsWith('/plugin-configs/browse') ? 'PluginConfigBrowseView' : null
  );
  if (schema && responses[schema]) {
    if (['/api/v1/settings', '/api/v1/settings/ai'].includes(path) && role !== 'admin') {
      json({ detail: 'Administrators only' }, 403); return true;
    }
    const value = responses[schema];
    if (req.method === 'PUT' || req.method === 'PATCH') Object.assign(value, input);
    json({ ...value, ...id ? { server_id: id } : {} }); return true;
  }
  if (path.endsWith('/schedule') || path.endsWith('/custom-commands') || path === '/api/v1/profile/passkeys') { json([]); return true; }
  if (path.endsWith('/files')) {
    json({ server_id: id, root: '/srv/cs2', path: '/srv/cs2', ssh_ok: true,
      files: [{ name: 'server.cfg', path: '/srv/cs2/server.cfg', type: 'file', size: 100,
        modified: Date.parse(FIXTURE_STAMP) / 1000, permissions: '-rw-r--r--', is_symlink: false }] });
    return true;
  }
  if (path.endsWith('/files/content')) {
    if (req.method === 'PUT') {
      fileContents.set(id, input.content); json({ success: true, message: 'Saved' });
    } else json({ path: '/srv/cs2/server.cfg', content: fileContents.get(id) ?? 'hostname "fixture"\n' });
    return true;
  }
  if (path === '/api/v1/auth/passkeys/register/options') { json({ challenge: 'fixture', rp: { id: '127.0.0.1', name: 'fixture' }, user: { id: 'MQ', name: currentUser.username, displayName: currentUser.username }, pubKeyCredParams: [] }); return true; }
  if (path === '/api/v1/settings/ai/test' || path === '/api/v1/profile/ai/test' || path === '/api/v1/setup/auto-setup') { json({ success: true, message: 'Fixture test complete', steps: [] }); return true; }
  if (path.endsWith('/plugin-configs/sources/1/scan')) {
    res.writeHead(200, { 'content-type': 'application/x-ndjson' }); res.end('{"type":"complete","files":0}\n'); return true;
  }
  if (path.endsWith('/console')) {
    json({ server_id: id, host: `fixture-${id}.invalid`, session_manager: 'tmux', ssh_ok: true,
      game_running: true, steamcmd_running: false, message: null }); return true;
  }
  if (path.endsWith('/console/pane')) {
    json({ server_id: id, kind: 'game', session_name: 'fixture', session_manager: 'tmux',
      ssh_ok: true, running: true, text: 'Fixture live console\n', heartbeat: FIXTURE_STAMP }); return true;
  }
  if (path === '/api/v1/operations/inbox/events') { stream(res, [['inbox', inbox]]); return true; }
  if (path.endsWith(`/operations/${FIXTURE_OPERATION}/events`)) {
    stream(res, [['progress', { operation_id: FIXTURE_OPERATION, sequence: '1', type: 'progress',
      kind: 'status', message: 'Fixture operation running', timestamp: FIXTURE_STAMP }]]); return true;
  }
  if (path === '/api/v1/assistant/runs/fixture-run/events') {
    stream(res, [['token_usage', { type: 'token_usage', payload: { input_tokens: 10, output_tokens: 2, total_tokens: 12 } }],
      ['assistant_delta', { type: 'assistant_delta', payload: { delta: 'Fixture assistant stream' } }]]); return true;
  }
  if (path.endsWith('/events')) { stream(res, [['progress', { type: 'progress', message: 'Fixture stream' }]]); return true; }
  if (path.endsWith('/files/upload')) { json({ success: true, message: 'Uploaded fixture' }); return true; }
  return false;
}

const app = createPerformanceFixture({ port, extraHandler, serverCount: 500, onReset: () => {
  responses = structuredClone(INITIAL_RESPONSES); fileContents.clear();
} });
app.on('request', (_req, res) => {
  stats.activeRequests += 1;
  res.on('close', () => { stats.activeRequests -= 1; });
});
app.on('upgrade', (req, socket) => {
  const token = String(req.headers.cookie ?? '').match(/upkk_access_token=fixture-(admin|member)-(a|b)/);
  const id = Number(req.url.match(/\/servers\/(\d+)\//)?.[1]);
  const key = req.headers['sec-websocket-key'];
  if (!token || !key || !id || (id % 2 ? 'a' : 'b') !== token[2]) {
    socket.end('HTTP/1.1 401 Unauthorized\r\nConnection: close\r\n\r\n'); return;
  }
  const accept = createHash('sha1').update(`${key}258EAFA5-E914-47DA-95CA-C5AB0DC85B11`).digest('base64');
  socket.write(`HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: ${accept}\r\n\r\n`);
  const send = value => { const bytes = Buffer.from(JSON.stringify(value)); socket.write(Buffer.concat([Buffer.from([0x81, bytes.length]), bytes])); };
  stats.activeSockets++; stats.openedSockets++;
  send({ type: 'connected' }); send({ type: 'output', data: 'Fixture terminal connected\r\n' });
  socket.on('data', chunk => {
    if ((chunk[0] & 15) === 8) { socket.end(Buffer.from([0x88, 0])); return; }
    send({ type: 'output', data: 'Fixture terminal input received\r\n' });
  });
  socket.on('error', () => undefined);
  socket.on('close', () => { stats.activeSockets--; stats.closedSockets++; });
});
app.listen(port, '127.0.0.1', () => console.log(`Cache Components fixture :${port}`));
