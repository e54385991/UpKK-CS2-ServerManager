// Loopback only. Deterministic tenants and 500 hosts; no real credentials or I/O.
import { createPerformanceFixture } from './performance.mock.mjs';
import { FIXTURE_STAMP, FIXTURE_OPERATION } from './helpers/cache-components-surfaces.mjs';
const port = Number(process.env.CC_MOCK_PORT ?? 38261);
const stats = { activeStreams: 0, openedStreams: 0, cancelledStreams: 0, activeRequests: 0 };
const coverage = { warmup: new Set(), measure: new Set() };
let phase = 'warmup';

function stream(res, frames) {
  stats.openedStreams += 1;
  stats.activeStreams += 1;
  res.writeHead(200, { 'content-type': 'text/event-stream', 'cache-control': 'no-store' });
  for (const [event, data] of frames) res.write(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`);
  const timer = setInterval(() => res.write(': keepalive\n\n'), 1000);
  res.on('close', () => {
    clearInterval(timer);
    stats.activeStreams -= 1;
    stats.cancelledStreams += 1;
  });
}

async function extraHandler({ req, res, path, input, actor, json, server, inbox }) {
  if (path === '/__test__/connections') {
    json({ ...stats, coverage: Object.fromEntries(Object.entries(coverage).map(([key, ids]) => [key, ids.size])) });
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
    stream(res, [['token_usage', { input_tokens: 10, output_tokens: 2, total_tokens: 12 }],
      ['assistant_delta', { text: 'Fixture assistant stream' }]]); return true;
  }
  if (path.endsWith('/files/upload')) { json({ success: true, message: 'Uploaded fixture' }); return true; }
  if (path.endsWith('/schedule') || path.endsWith('/maps') || path.endsWith('/help')) {
    // Specific projections are filled below as the all-page contract sweep reaches them.
    return false;
  }
  return false;
}

const app = createPerformanceFixture({ port, extraHandler, serverCount: 500 });
app.on('request', (_req, res) => {
  stats.activeRequests += 1;
  res.on('close', () => { stats.activeRequests -= 1; });
});
app.listen(port, '127.0.0.1', () => console.log(`Cache Components fixture :${port}`));
