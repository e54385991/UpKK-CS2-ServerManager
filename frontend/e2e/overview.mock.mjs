// Isolated telemetry fixture. This server never connects to a database or game host.
import { createServer } from "node:http";

const port = Number(process.env.OVERVIEW_MOCK_PORT ?? 38121);
const servers = [1, 2].map(id => ({
  id, name: `fixture-server-${id}`, host: `host-${id}.invalid`, game_port: 27015,
  ssh_user: "fixture", status: "running", default_map: "de_dust2", max_players: 32,
}));
const snapshots = [...servers].reverse().map(server => ({
  server_id: server.id, cached: true, success: true, system_type: "Linux",
  architecture: "x86_64", cpu_model: `fixture-cpu-${server.id}`, cpu_cores: 8,
  kernel_version: "6.8.0", distribution: "debian", distribution_version: "12",
  distribution_pretty_name: "Debian GNU/Linux 12", memory_total_bytes: 17179869184,
  memory_available_bytes: 8589934592, collected_at: "2026-09-06T00:00:00Z",
}));
const inbox = { items: [], failed_items: [], market_import_items: [], active_count: 0,
  running_count: 0, failed_count: 0, failed_retention_days: 7 };
let hostRequests = 0;
let summaryRequests = 0;
let outcome = "success";
const waiting = new Set();

const app = createServer(async (req, res) => {
  const path = new URL(req.url, `http://127.0.0.1:${port}`).pathname;
  const json = (value, status = 200) => {
    if (res.destroyed) return;
    res.writeHead(status, { "content-type": "application/json" });
    res.end(JSON.stringify(value));
  };
  if (path === "/__test__/state") return json({ hostRequests, summaryRequests });
  if (path === "/__test__/reset") {
    for (const release of waiting) release();
    waiting.clear();
    hostRequests = 0;
    summaryRequests = 0;
    outcome = "success";
    return json({ ok: true });
  }
  if (path === "/__test__/release") {
    outcome = new URL(req.url, "http://fixture").searchParams.get("outcome") ?? "success";
    for (const release of waiting) release();
    waiting.clear();
    return json({ ok: true });
  }
  if (path === "/api/v1/overview/host-system-info") {
    hostRequests += 1;
    await new Promise(resolve => {
      waiting.add(resolve);
      res.on("close", () => { waiting.delete(resolve); resolve(); });
    });
    return outcome === "failure"
      ? json({ detail: "Simulated telemetry failure" }, 503)
      : json({ servers: outcome === "empty" ? [] : snapshots, timestamp: "2026-09-06T00:00:00Z" });
  }
  if (path === "/api/v1/overview/summary") {
    summaryRequests += 1;
    return json({ total: 2, running: 2, attention: 0, capacity: 64,
      ssh_connections: 0, ssh_in_use: 0, ssh_idle: 0, ssh_leases: 0 });
  }
  if (path === "/api/v1/servers") return json(servers);
  if (path === "/api/v1/auth/me") return json({ id: 1, username: "fixture-admin", email: null,
    is_admin: true, is_active: true });
  if (path === "/health") return json({ status: "ok", version: "fixture" });
  if (path === "/api/v1/ssh-pool") return json({ connections: 0, in_use: 0, idle: 0, leases: 0 });
  if (path === "/api/v1/operations/inbox") return json(inbox);
  if (path === "/api/v1/operations/inbox/events") {
    res.writeHead(200, { "content-type": "text/event-stream" });
    res.write(`event: inbox\ndata: ${JSON.stringify(inbox)}\n\n`);
    const timer = setInterval(() => res.write(": ping\n\n"), 1000);
    res.on("close", () => clearInterval(timer));
    return;
  }
  if (path === "/api/v1/plugins/market/ai-imports") return json([]);
  return json({ detail: `Unused fixture route: ${path}` }, 404);
});
app.listen(port, "127.0.0.1", () => console.log(`Overview mock backend :${port}`));
