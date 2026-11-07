// Loopback-only personal registry fixture; no database, game server, or credentials.
import { createServer } from 'node:http';
const port = Number(process.env.PROFILE_MOCK_PORT ?? 38161);
let commands = [];
let failSave = false;
const app = createServer(async (req, res) => {
  const path = new URL(req.url, 'http://fixture').pathname;
  const json = (body, status = 200) => { res.writeHead(status, { 'content-type': 'application/json' }); res.end(JSON.stringify(body)); };
  if (path === '/health') return json({ status: 'healthy', version: 'fixture' });
  if (path === '/__test__/reset') { commands = []; failSave = false; return json({ ok: true }); }
  if (path === '/__test__/fail') { failSave = true; return json({ ok: true }); }
  if (path === '/__test__/commands') return json(commands);
  if (path === '/api/v1/auth/me') return json({ id: 1, username: 'fixture-member', email: null, is_admin: false, is_active: true });
  if (path === '/api/v1/profile/game-commands') {
    if (req.method === 'PUT') {
      const chunks = [];
      for await (const chunk of req) chunks.push(chunk);
      if (failSave) return json({ detail: 'Fixture persistence failed' }, 503);
      commands = JSON.parse(Buffer.concat(chunks).toString()).commands;
    }
    return json({ commands });
  }
  if (path === '/api/v1/profile') return json({ id: 1, username: 'fixture-member', email: null, is_admin: false, is_active: true, created_at: null,
    steamcmd_max_retries: 20, steamcmd_max_retries_default: 20, steamcmd_max_retries_limit: 100,
    has_steam_api_key: false, has_github_token: false, has_api_key: true, google_linked: false });
  if (path === '/api/v1/profile/s3') return json({ enabled: false, has_secret: false, is_configured: false, retention_count: 10, use_ssl: true });
  if (path === '/api/v1/profile/ai') return json({ mode: 'global', effective_enabled: false, effective_source: 'none', api_key_configured: false });
  if (path === '/api/v1/auth/passkeys') return json({ items: [] });
  if (path === '/api/v1/announcements') return json({ items: [], cs2_update_notice: null });
  if (path === '/api/v1/operations/inbox') return json({ items: [], failed_items: [], market_import_items: [], active_count: 0, running_count: 0, failed_count: 0, failed_retention_days: 7 });
  if (path.endsWith('/events')) { res.writeHead(200, { 'content-type': 'text/event-stream' }); res.write(': fixture\n\n'); res.on('close', () => res.end()); return; }
  return json({ detail: 'Unused fixture route' }, 404);
});
app.listen(port, '127.0.0.1', () => console.log(`Profile mock :${port}`));
