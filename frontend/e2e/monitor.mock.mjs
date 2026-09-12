// Isolated system-monitoring fixture. Never connects to Redis, SSH, or a database.
import { createServer } from "node:http";

const port = Number(process.env.MONITOR_MOCK_PORT ?? 38141);
const stamp = "2026-09-13T04:00:00.000Z";
let scenario = "healthy";
let enabled = true;
let monitorGets = 0;
let lastRange = "1h";

function point(offsetSec, extra = {}) {
  const ts = new Date(Date.parse(stamp) - offsetSec * 1000).toISOString();
  return {
    ts, request_count: 24, requests_per_minute: 48, status_5xx: 0, error_rate: 0,
    latency_p50_ms: 18, latency_p95_ms: 42, latency_p99_ms: 80, latency_max_ms: 90,
    latency_partial: false, cpu_percent: 6.2, rss_bytes: 180_000_000, rss_peak_bytes: 190_000_000,
    loop_lag_p95_ms: 4, db_checked_out: 2, db_capacity: 15, redis_ping_ms: 1.4, redis_connected: true,
    queue_running: 0, queue_queued: 0, oldest_queue_ms: null, failed_tasks: 0, unhandled_exceptions: 0,
    fd_open: 32, fd_limit: 1024, ...extra,
  };
}

function seriesFor(kind) {
  if (kind === "empty") return [];
  const points = [];
  for (let i = 11; i >= 0; i -= 1) {
    const spike = kind === "spike" && i <= 2;
    const redisDown = kind === "redis-down";
    points.push(point(i * 30, {
      latency_p95_ms: spike ? 1200 : 42,
      request_count: spike ? 40 : 24,
      status_5xx: spike ? 6 : 0,
      error_rate: spike ? 0.15 : 0,
      failed_tasks: spike ? 2 : 0,
      redis_connected: redisDown ? false : true,
      redis_ping_ms: redisDown ? null : 1.4,
    }));
  }
  return points;
}

function snapshot() {
  return {
    format: "upkk-panel-performance", version: 1, captured_at: stamp,
    priority: ["requests", "process"],
    requests: {
      window_seconds: 10, sample_count: 24, requests_per_minute: 48, status_2xx: 24, status_4xx: 0,
      status_5xx: scenario === "spike" ? 6 : 0, error_rate: scenario === "spike" ? 0.15 : 0,
      latency_ms: { p50: 18, p95: scenario === "spike" ? 1200 : 42, p99: 80, max: 90 },
      slow_request_count: scenario === "spike" ? 8 : 0, slow_request_threshold_ms: 500,
      by_route: [{ method: "GET", route: "/api/v1/servers", count: 12, error_count: scenario === "spike" ? 2 : 0, latency_ms: { p50: 20, p95: scenario === "spike" ? 1100 : 40, p99: 80, max: 90 } }],
      in_flight: 1, unhandled_exceptions: 0,
    },
    process: { pid: 9, uptime_seconds: 3600, rss_bytes: 180000000, cpu_percent: 6.2, threads: 12, asyncio_tasks: 8, event_loop_lag_ms: 4, fd_open: 32, fd_limit: 1024 },
    database: { pool_size: 5, max_overflow: 10, checked_out: 2, checked_in: 3, overflow: 0, capacity: 15 },
    redis: { connected: scenario !== "redis-down", ping_ms: scenario === "redis-down" ? null : 1.4, pool_max_connections: 16 },
    ssh_pool: { connections: 1, in_use: 0, idle: 1, leases: 0 },
    limiters: [{ name: "ssh_probes", global_limit: 4, per_key_limit: 1, active_keys: 0, borrowers: 0, executing: 0, waiting: 0 }],
    operations: { running: 0, queued: scenario === "spike" ? 3 : 0, runners: 0, subscribers: 0, tracked: 0 },
    runtime: { version: "fixture", python: "3.14", fastapi: "0.0", git_sha: "fixture", build_time: stamp, worker_pid: 9 },
  };
}

function monitor(range = "1h") {
  const series = seriesFor(scenario);
  const alerts = [];
  if (scenario === "spike") {
    alerts.push({
      id: "request_p95", severity: "critical", metric: "request_p95",
      title: "Request p95 is elevated", detail: "Request p95 reached 1200 ms.",
      guidance: "Inspect slow routes.", threshold: ">=1000 ms", value: 1200, since: stamp,
    });
  }
  if (scenario === "redis-down") {
    alerts.push({
      id: "redis", severity: "critical", metric: "redis",
      title: "Redis is disconnected", detail: "The panel could not ping Redis.",
      guidance: "Check Redis.", threshold: "connection failed", value: null, since: stamp,
    });
  }
  return {
    range,
    instance_id: "9-fixture",
    instances: [{ instance_id: "9-fixture", last_seen_at: stamp, current: true }],
    status: {
      enabled, running: enabled, stale: false,
      history_available: scenario !== "empty",
      history_error: scenario === "empty" ? "unavailable" : null,
      config_sync_error: null, last_sample_at: enabled ? stamp : null,
      stopped_at: enabled ? null : stamp, instance_id: "9-fixture",
      sample_interval_seconds: 10, dropped_error_details: 0, truncated_summaries: 0,
      collection_ms: 4.2, collecting: enabled,
    },
    snapshot: scenario === "empty" ? null : snapshot(),
    series, alerts: enabled ? alerts : [],
    error_groups: scenario === "spike" ? [{
      source: "request", error_code: "unhandled", severity: "error", exception_type: "RuntimeError",
      route: "/api/v1/servers", count: 2, first_ts: stamp, last_ts: stamp,
      summary: "fixture failure", request_id: "req-fixture", operation_id: null,
      frames: [{ file: "api/routes/v1/servers.py", function: "list_servers", line: 12 }],
    }] : [],
    integrity: { sample_interval_seconds: 10, display_bucket_seconds: 30, point_count: series.length, gap: scenario === "empty", partial_latency: false },
  };
}

const settings = {
  default_proxy_mode: "panel", github_proxy_url: null, plugin_download_cache_enabled: true,
  plugin_download_cache_path: null, plugin_download_cache_files: 0, plugin_download_cache_bytes: 0,
  plugin_download_cache_max_age_days: 30, plugin_download_cache_max_megabytes: 4096,
  captcha_enabled: true, registration_enabled: true, client_ip_header: "X-Forwarded-For",
  log_level: "ERROR", effective_log_level: "ERROR", panel_monitoring_enabled: true,
  has_global_github_token: false, global_github_token_prefix: null, github_token_verification: null,
  email_enabled: false, email_provider: "smtp", email_from_address: null, email_from_name: null,
  smtp_host: null, smtp_port: 587, smtp_username: null, smtp_use_tls: true,
  has_smtp_password: false, has_gmail_credentials: false, has_gmail_token: false, gmail_ready: false,
  updated_at: stamp,
};

const inbox = { items: [], failed_items: [], market_import_items: [], active_count: 0, running_count: 0, failed_count: 0, failed_retention_days: 7 };

const app = createServer(async (req, res) => {
  const url = new URL(req.url, `http://127.0.0.1:${port}`);
  const json = (value, status = 200) => {
    if (res.destroyed) return;
    res.writeHead(status, { "content-type": "application/json" });
    res.end(JSON.stringify(value));
  };
  let text = "";
  for await (const part of req) text += part;
  const input = text ? JSON.parse(text) : {};
  if (url.pathname === "/__test__/scenario") {
    scenario = url.searchParams.get("name") ?? "healthy";
    enabled = scenario !== "disabled";
    settings.panel_monitoring_enabled = enabled;
    return json({ ok: true, scenario });
  }
  if (url.pathname === "/__test__/state") return json({ monitorGets, enabled, scenario, lastRange });
  if (url.pathname === "/health") return json({ status: "ok", version: "fixture" });
  if (url.pathname === "/api/v1/auth/me") {
    return json({ id: 1, username: "fixture-admin", email: null, is_admin: true, is_active: true });
  }
  if (url.pathname === "/api/v1/operations/inbox") return json(inbox);
  if (url.pathname === "/api/v1/operations/inbox/events") {
    res.writeHead(200, { "content-type": "text/event-stream" });
    res.write(`event: inbox\ndata: ${JSON.stringify(inbox)}\n\n`);
    const timer = setInterval(() => res.write(": ping\n\n"), 1000);
    res.on("close", () => clearInterval(timer));
    return;
  }
  if (url.pathname === "/api/v1/ssh-pool") return json({ connections: 0, in_use: 0, idle: 0, leases: 0 });
  if (url.pathname === "/api/v1/plugins/market/ai-imports") return json([]);
  if (url.pathname === "/api/v1/overview/summary") {
    return json({ total: 0, running: 0, attention: 0, capacity: 0, ssh_connections: 0, ssh_in_use: 0, ssh_idle: 0, ssh_leases: 0 });
  }
  if (url.pathname === "/api/v1/settings/ai") {
    return json({
      enabled: false, base_url: "", model: "", api_protocol: "chat_completions",
      api_key_configured: false, admin_prompt: null, private_endpoint_allowlist: [],
      max_completion_tokens: 2048, token_limit_parameter: "max_completion_tokens",
      context_window_tokens: 262144, requests_per_minute: 30, request_timeout_seconds: 60,
      history_retention_days: 7, max_provider_rounds: 200, max_tool_calls_per_round: 200,
      provider_tested: false, tool_calling_tested: false, streaming_tested: false,
    });
  }
  if (url.pathname === "/api/v1/settings" && req.method === "PUT") {
    if (Object.hasOwn(input, "panel_monitoring_enabled")) {
      enabled = Boolean(input.panel_monitoring_enabled);
      settings.panel_monitoring_enabled = enabled;
    }
    return json({ ...settings, panel_monitoring_enabled: enabled });
  }
  if (url.pathname === "/api/v1/settings") return json({ ...settings, panel_monitoring_enabled: enabled });
  if (url.pathname === "/api/v1/diagnostics/monitor") {
    monitorGets += 1;
    lastRange = url.searchParams.get("range") ?? "1h";
    return json(monitor(lastRange));
  }
  if (url.pathname === "/api/v1/diagnostics/errors") {
    const cursor = url.searchParams.get("cursor");
    const source = url.searchParams.get("source");
    const all = scenario === "spike" ? [
      {
        id: "err-1", ts: stamp, source: "request", severity: "error",
        error_code: "unhandled", exception_type: "RuntimeError", summary: "fixture failure",
        truncated: false, route: "/api/v1/servers", operation_id: null, request_id: "req-fixture",
        frames: [{ file: "api/routes/v1/servers.py", function: "list_servers", line: 12 }],
      },
      {
        id: "err-2", ts: new Date(Date.parse(stamp) - 30_000).toISOString(), source: "request", severity: "error",
        error_code: "unhandled", exception_type: "RuntimeError", summary: "fixture failure later",
        truncated: false, route: "/api/v1/servers", operation_id: null, request_id: "req-fixture-2",
        frames: [{ file: "api/routes/v1/servers.py", function: "list_servers", line: 12 }],
      },
      {
        id: "err-3", ts: new Date(Date.parse(stamp) - 60_000).toISOString(), source: "task", severity: "error",
        error_code: "failed", exception_type: "RuntimeError", summary: "job failed",
        truncated: false, route: null, operation_id: "op-fixture", request_id: null, frames: [],
      },
    ] : [];
    const filtered = source ? all.filter((item) => item.source === source) : all;
    const page = cursor ? filtered.slice(1) : filtered.slice(0, 1);
    return json({
      items: page,
      next_cursor: !cursor && filtered.length > 1 ? "1" : null,
      dropped: scenario === "spike" ? 4 : 0,
      truncated: false,
    });
  }
  if (url.pathname === "/api/v1/diagnostics") return json(snapshot());
  return json({ detail: `Unused fixture route: ${url.pathname}` }, 404);
});
app.listen(port, "127.0.0.1", () => console.log(`Monitor mock backend :${port}`));
