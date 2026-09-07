// Isolated browser-test backend. No database, GitHub, AI provider or SSH calls.
import { createServer } from "node:http";

const tasks = [];
const aiSettings = { enabled: false, base_url: "https://provider.example/v1", model: "test-model", api_protocol: "chat_completions", api_key_configured: true, private_endpoint_allowlist: [], max_completion_tokens: 2048, token_limit_parameter: "max_completion_tokens", context_window_tokens: 262144, requests_per_minute: 60, request_timeout_seconds: 60, history_retention_days: 7, max_provider_rounds: 200, max_tool_calls_per_round: 200, provider_tested: true, tool_calling_tested: true, streaming_tested: true };
const verification = { valid: true, account: "test-admin", checked_at: new Date().toISOString(), core_remaining: 4900, search_remaining: 29, core_reset: 2000000000, search_reset: 2000000000, message: "GitHub token verified" };
const plugin = { id: 1, title: "AI Test Plugin", description: "Test installation documentation", author: "example", version: "1.0", category: "utility", framework: "counterstrikesharp", tags: null, is_recommended: false, icon_url: null, github_url: "https://github.com/example/plugin", custom_install_path: null, download_count: 0, install_count: 0, dependencies: [], ai_metadata: { model: "test-model", reviewed: false, installation: { asset_glob: "*.zip", source_prefix: "", target_path: null }, requirements: [], sources: [{ path: "README.md", commit: "a".repeat(40) }] } };

createServer(async (req, res) => {
  const path = new URL(req.url, "http://localhost").pathname;
  let body = "";
  for await (const chunk of req) body += chunk;
  const input = body ? JSON.parse(body) : {};
  const json = (value, status = 200) => { res.writeHead(status, { "content-type": "application/json" }); res.end(JSON.stringify(value)); };
  const inbox = () => ({ items: [], failed_items: [], market_import_items: tasks, active_count: 0, running_count: 0, failed_count: 0, failed_retention_days: 7 });
  if (path.endsWith("/events")) {
    res.writeHead(200, { "content-type": "text/event-stream" });
    const send = () => {
      const value = path.includes("operations/inbox") ? inbox() : tasks.find(task => path.includes(task.operation_id));
      res.write(`event: ${path.includes("operations/inbox") ? "inbox" : "snapshot"}\ndata: ${JSON.stringify(value)}\n\n`);
    };
    send();
    const timer = setInterval(send, 1000);
    res.on("close", () => clearInterval(timer));
    return;
  }
  if (path === "/api/v1/auth/me") return json({ id: 1, username: "test-admin", is_admin: true, is_active: true, email: null });
  if (path === "/api/v1/servers") return json([{ id: 1, name: "fixture-server", host: "fixture.invalid", game_port: 27015, status: "running", max_players: 32 }]);
  if (path === "/api/v1/profile") return json({ id: 1, username: "test-admin", is_admin: true, is_active: true, has_github_token: true, github_token_prefix: "ghp_…", steamcmd_max_retries: 3, steamcmd_max_retries_default: 3, steamcmd_max_retries_limit: 10 });
  if (path === "/api/v1/servers/1/plugins") return json([]);
  if (path === "/api/v1/plugins/github/releases") {
    const missing = new URL(req.url, "http://localhost").searchParams.get("repo_url")?.includes("missing");
    return json({ detail: missing ? "Failed to fetch releases: HTTP 404: Not Found" : 'Failed to fetch releases: HTTP 401: { "message": "Bad credentials", "status": "401" }' }, 400);
  }
  if (path === "/api/v1/operations/inbox") return json(inbox());
  if (path === "/api/v1/settings") return json({ default_proxy_mode: "direct", effective_log_level: "INFO", has_global_github_token: true, global_github_token_prefix: "ghp_…", github_token_verification: verification, email_enabled: false, email_provider: "smtp", smtp_use_tls: true, has_smtp_password: false, has_gmail_credentials: false, has_gmail_token: false, gmail_ready: false });
  if (path === "/api/v1/settings/test-github-token") return json(verification);
  if (path === "/api/v1/settings/ai") {
    if (req.method === "PUT") {
      if (!Number.isInteger(input.requests_per_minute) || input.requests_per_minute < 1 || input.requests_per_minute > 10000) return json({ detail: "RPM must be between 1 and 10000" }, 422);
      Object.assign(aiSettings, input);
    }
    return json(aiSettings);
  }
  if (path === "/api/v1/plugins/market/ai-imports/readiness") return json({ token_valid: true, token_account: "test-admin", token_message: "Verified", ai_configured: true, ai_model: "test-model" });
  if (path === "/api/v1/plugins/market/ai-imports") {
    if (req.method === "POST") {
      const task = { operation_id: input.request_id, status: "running", command: "AI import", options: input.options, created_at: new Date().toISOString(), started_at: new Date().toISOString(), completed_at: null, phase: "searching", message: "Searching maintained CS2 plugins", current_repository: plugin.github_url, model: "test-model", stop_reason: null, retry_at: null, cancel_requested: false, items: [], events: [] };
      tasks.unshift(task);
      return json(task, 202);
    }
    return json(tasks);
  }
  if (path.endsWith("/review")) { plugin.ai_metadata = input.metadata; return json({ metadata: plugin.ai_metadata }); }
  if (path.includes("/ai-imports/")) {
    const task = tasks.find(item => path.includes(item.operation_id));
    if (path.endsWith("/cancel") && task) { task.status = "cancelled"; task.cancel_requested = true; task.completed_at = new Date().toISOString(); }
    return json(task ?? {}, task ? 200 : 404);
  }
  if (path === "/api/v1/plugins/market/categories") return json({ items: [] });
  if (path === "/api/v1/plugins/market/1") return json(plugin);
  if (path === "/api/v1/plugins/market") return json({ items: [plugin], total: 1, limit: 20, offset: 0 });
  return json({ detail: "Unused mock endpoint" }, 404);
}).listen(38111, "127.0.0.1", () => process.stdout.write("AI import mock backend :38111\n"));
