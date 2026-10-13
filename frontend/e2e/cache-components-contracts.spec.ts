import { test, expect, type BrowserContext, type APIRequestContext } from "@playwright/test";
import en from "../src/i18n/messages/en-US.json" with { type: "json" };
import zh from "../src/i18n/messages/zh-CN.json" with { type: "json" };
import { FIXTURE_OPERATION } from "./helpers/cache-components-surfaces.mjs";
import type { DescriptionSyncJob } from "../src/modules/plugins/types";

const origin = "http://127.0.0.1:31961";
const mock = "http://127.0.0.1:38261";
const state = async (request: APIRequestContext) => (await request.get(`${mock}/__test__/state`)).json();
const connections = async (request: APIRequestContext) => (await request.get(`${mock}/__test__/connections`)).json();
async function login(context: BrowserContext, locale: string, actor: string | null = "admin-a") {
  await context.clearCookies();
  await context.addCookies([{ name: "locale", value: locale, url: origin },
    ...actor ? [{ name: "upkk_access_token", value: `fixture-${actor}`, httpOnly: true, url: origin }] : []]);
}
test.beforeEach(async ({ context, request }, info) => {
  await request.post(`${mock}/__test__/reset`);
  await login(context, info.project.name.startsWith("zh-CN") ? "zh-CN" : "en-US");
});

const handlers = [
  ["GET", "/ai-assistant?action=workspace"], ["POST", "/ai-provider-test"], ["GET", "/ai-settings"],
  ["GET", "/ai-stream/runs/fixture-run"], ["GET", "/cleanup-stream/servers/1/scan"],
  ["GET", "/cleanup-stream/servers/1/system"], ["POST", "/files-upload/servers/1"],
  ["GET", `/ops-stream/initialized-servers/1/operations/${FIXTURE_OPERATION}`],
  ["GET", "/ops-stream/operations/inbox"], ["GET", `/ops-stream/plugin-descriptions/${FIXTURE_OPERATION}`],
  ["GET", `/ops-stream/plugin-imports/${FIXTURE_OPERATION}`],
  ["GET", `/ops-stream/servers/1/operations/${FIXTURE_OPERATION}`],
  ["POST", "/passkeys?action=register-options"], ["POST", "/plugin-config-scan/servers/1/sources/1"],
  ["GET", "/server-ops/inbox"], ["GET", "/server-ops/servers/1"],
  ["DELETE", "/server-record/servers/1"], ["POST", "/setup-stream/run"],
] as const;

test("all 18 handlers preserve methods, bearer forwarding and stream cancellation", async ({ page, request }) => {
  await page.goto("/cc-navigation");
  for (const [method, path] of handlers) {
    const eventSource = method === "GET" && /^\/(ai-stream|cleanup-stream|ops-stream)\//.test(path);
    const responsePromise = eventSource ? page.waitForResponse(response =>
      new URL(response.url()).pathname === path) : null;
    const result = await page.evaluate(async ({ method, path, eventSource }) => {
      if (eventSource) {
        const source = new EventSource(path);
        try {
          await new Promise<void>((resolve, reject) => {
            const timer = setTimeout(() => reject(new Error(`Stream ${path}: no data`)), 10000);
            const done = () => { clearTimeout(timer); resolve(); };
            source.onmessage = done;
            for (const type of ["progress", "inbox", "token_usage", "assistant_delta"]) {
              source.addEventListener(type, done, { once: true });
            }
            source.onerror = () => { clearTimeout(timer); reject(new Error(`Stream ${path}: connection failed`)); };
          });
          await new Promise(resolve => setTimeout(resolve, 0));
        } finally { source.close(); }
        return null;
      }
      const controller = new AbortController();
      let body: string | FormData | undefined;
      if (method === "POST" || method === "PATCH") {
        if (path.startsWith("/files-upload")) {
          body = new FormData(); body.append("file", new Blob(["fixture upload"]), "fixture.txt");
        } else body = "{}";
      }
      const response = await fetch(path, { method, body, signal: controller.signal,
        headers: typeof body === "string" ? { "content-type": "application/json" } : {} });
      const type = response.headers.get("content-type") ?? "";
      if (type.includes("text/event-stream")) {
        const reader = response.body!.getReader(); await reader.read(); controller.abort();
        await reader.cancel().catch(() => undefined);
      } else await response.text();
      return { status: response.status, type, redirected: response.redirected };
    }, { method, path, eventSource });
    const response = responsePromise ? await responsePromise : null;
    expect(response?.status() ?? result?.status, `${method} ${path}`).toBe(200);
    expect(response ? response.request().redirectedFrom() !== null : result?.redirected, path).toBe(false);
    if (response) expect(await response.headerValue("content-type")).toContain("text/event-stream");
    if (eventSource || result?.type.includes("text/event-stream")) {
      // A document navigation delivers the actual HTTP disconnect to the proxy.
      // Soft navigation of the real assistant component is covered below.
      await page.goto("/deployment-tutorial");
      await expect(page).toHaveURL(url => url.pathname === "/deployment-tutorial");
      await expect.poll(async () => {
        const current = await connections(request);
        return { activeStreams: current.activeStreams, activeStreamPaths: current.activeStreamPaths };
      }, {
        message: `${method} ${path} must release its upstream stream`,
      }).toEqual({ activeStreams: 0, activeStreamPaths: {} });
      await page.goto("/cc-navigation");
    }
  }
  const calls = (await state(request)).requests.filter((row: { path: string }) => row.path.startsWith("/api/"));
  expect(calls.every((row: { actor: string }) => row.actor === "Bearer fixture-admin-a")).toBe(true);
  await expect.poll(async () => (await connections(request)).activeStreams).toBe(0);
});

test("all handlers reject absent credentials before any locale rewriting", async ({ context, page }) => {
  await login(context, "en-US", null);
  await page.goto("/cc-navigation");
  for (const [method, path] of handlers) {
    const response = await page.request.fetch(path, { method, data: method === "POST" || method === "PATCH" ? {} : undefined });
    expect(response.status(), `${method} ${path}`).toBe(401);
  }
});

test("anonymous, forged and expired cookies retain the public login return query", async ({ context, page }) => {
  for (const actor of [null, "forged", "expired"] as const) {
    await login(context, "zh-CN", actor);
    await page.goto("/servers/1/files?path=%2Fsrv%2Fcs2");
    await expect(page).toHaveURL(url => url.pathname === "/login");
    expect(new URL(page.url()).searchParams.get("next")).toBe("/servers/1/files?path=%2Fsrv%2Fcs2");
    await expect(page.getByTestId("public-auth-frame")).toBeVisible();
    await expect(page.locator("html")).toHaveAttribute("lang", "zh-CN");
  }
});

test("internal and encoded locale prefixes cannot bypass authentication", async ({ context, page }) => {
  await login(context, "en-US", null);
  for (const path of ["/en-US/settings", "/zh-CN/settings", "/%65n-US/%73ettings"]) {
    await page.goto(path);
    await expect(page).toHaveURL(url => url.pathname === "/login" && url.searchParams.get("next") === "/settings");
  }
});

test("roles and tenants stay isolated across fresh requests", async ({ context, page }, info) => {
  const locale = info.project.name.startsWith("zh-CN") ? "zh-CN" : "en-US";
  const messages = locale === "en-US" ? en : zh;
  for (const actor of ["admin-a", "member-a", "admin-b", "member-b"]) {
    await login(context, locale, actor);
    const id = actor.endsWith("a") ? 1 : 2;
    await page.goto(`/servers/${id}/monitoring`);
    await expect(page.getByTestId("monitoring-form")).toBeVisible();
    await expect(page.locator("h1")).toContainText(`fixture-${actor.at(-1)}-server-${id}`);
    await page.goto("/settings");
    if (actor.startsWith("member")) await expect(page.getByText(messages.settings.forbidden, { exact: true })).toBeVisible();
    else await expect(page.getByTestId("settings-category-nav")).toBeVisible();
    await page.goto(`/servers/${id === 1 ? 2 : 1}/monitoring`);
    await expect(page.locator("h1")).not.toContainText(`fixture-${id === 1 ? "b" : "a"}-server`);
    await expect(page.getByTestId("monitoring-form")).toHaveCount(0);
  }
});

test("language changes keep public path, query, hash and navigation selection", async ({ page }, info) => {
  const wasChinese = info.project.name.startsWith("zh-CN");
  await page.goto("/servers/1/config?fixture=1#language-test");
  await expect(page.getByTestId("game-config-form")).toBeVisible();
  await page.getByRole("button", { name: wasChinese ? zh.shell.language : en.shell.language, exact: true }).click();
  await page.getByRole("menuitemradio", { name: wasChinese ? "English" : "简体中文", exact: true }).click();
  await expect(page.locator("html")).toHaveAttribute("lang", wasChinese ? "en-US" : "zh-CN");
  const url = new URL(page.url());
  expect(url.pathname).toBe("/servers/1/config"); expect(url.searchParams.get("fixture")).toBe("1");
  expect(url.hash).toBe("#language-test");
  const activeConfig = page.locator('[data-workspace-category="config"]').filter({ visible: true });
  await expect(activeConfig).toHaveCount(1);
  await expect(activeConfig).toHaveAttribute("aria-current", "page");
});

test("native navigation clicks keep streamed link hints consistent during hydration", async ({ page }, info) => {
  const messages = info.project.name.startsWith("zh-CN") ? zh : en;
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(String(error)));
  page.on("console", message => {
    if (message.type() === "error") errors.push(message.text());
  });
  for (const path of ["/overview", "/servers/1/monitoring"]) {
    await page.goto(path, { waitUntil: "commit" });
    const desktop = info.project.name.endsWith("1440");
    if (!desktop) await page.getByRole("button", { name: messages.shell.openNav, exact: true }).click();
    const scope = page.getByTestId(desktop ? "console-sidebar" : "console-mobile-drawer");
    await expect(scope).toBeVisible();
    await scope.evaluate(element => {
      const link = element.querySelector<HTMLAnchorElement>('a[data-nav-key="servers"]');
      if (!link) throw new Error("Missing server navigation link");
      link.click();
    });
    await expect(page).toHaveURL(url => url.pathname === "/servers");
    await expect(page.locator('a[href="/servers/1"]').filter({ visible: true }).first()).toBeVisible();
  }
  expect(errors).toEqual([]);
});

test("admin and shared navigation choose one most specific selected entry", async ({ page, request }, info) => {
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(String(error)));
  page.on("console", message => { if (message.type() === "error") errors.push(message.text()); });
  const messages = info.project.name.startsWith("zh-CN") ? zh : en;
  const desktop = info.project.name.endsWith("1440");
  const scope = page.getByTestId(desktop ? "console-sidebar" : "console-mobile-drawer");
  await request.post(`${mock}/__test__/reset`, { data: { rules: { "/api/v1/auth/me": { delay: 3000 } } } });
  await page.goto("/settings", { waitUntil: "commit" });
  if (!desktop) await page.getByRole("button", { name: messages.shell.openNav, exact: true }).click();
  await scope.locator('a[data-nav-key="discord"]').click();
  await expect(page).toHaveURL(url => url.pathname === "/settings/discord");
  if (!desktop) await page.getByRole("button", { name: messages.shell.openNav, exact: true }).click();
  await expect(scope.locator('a[data-nav-key="settings"]')).toBeVisible();
  await expect(scope.locator('a[data-nav-key][aria-current="page"]')).toHaveCount(1);
  if (!desktop) await scope.getByRole("button", { name: messages.shell.closeNav, exact: true }).click();
  await request.post(`${mock}/__test__/reset`);
  for (const [key, path] of [["settings", "/settings"], ["discord", "/settings/discord"], ["audit", "/audit"]]) {
    if (!desktop) await page.getByRole("button", { name: messages.shell.openNav, exact: true }).click();
    await scope.locator(`a[data-nav-key="${key}"]`).click();
    await expect(page).toHaveURL(url => url.pathname === path);
    if (!desktop) await page.getByRole("button", { name: messages.shell.openNav, exact: true }).click();
    await expect(scope.locator('a[data-nav-key][aria-current="page"]')).toHaveCount(1);
    await expect(scope.locator(`a[data-nav-key="${key}"]`)).toHaveAttribute("aria-current", "page");
    if (!desktop) await scope.getByRole("button", { name: messages.shell.closeNav, exact: true }).click();
  }
  expect(errors).toEqual([]);
});

test("unknown paths and missing IDs render a localized 404", async ({ page }, info) => {
  const chinese = info.project.name.startsWith("zh-CN");
  for (const path of ["/missing-fixture-page", "/servers/501", "/servers/not-an-id"]) {
    await page.goto(path);
    await expect(page.getByText("404", { exact: true })).toBeVisible();
    await expect(page.locator("html")).toHaveAttribute("lang", chinese ? "zh-CN" : "en-US");
  }
});

test("announcement delays and backend failures leave the shell and fast data available", async ({ page, request }) => {
  await request.post(`${mock}/__test__/reset`, { data: { rules: { "/api/v1/announcements": { gate: true } } } });
  await page.goto("/overview", { waitUntil: "commit" });
  await expect(page.getByTestId("overview-stats")).toBeVisible();
  const feed = (await state(request)).requests.find((row: { path: string }) => row.path === "/api/v1/announcements");
  expect(feed.ended).toBeNull();
  await request.post(`${mock}/__test__/release`, { data: { path: "/api/v1/announcements" } });
  await request.post(`${mock}/__test__/reset`, { data: { rules: { "/api/v1/overview/summary": { status: 503 } } } });
  await page.reload();
  await expect(page.locator('section[data-route="/overview"]')).toBeVisible();
  await expect(page.getByTestId("overview-stats")).toHaveCount(0);
  await request.post(`${mock}/__test__/reset`); await page.reload();
  await expect(page.getByTestId("overview-stats")).toBeVisible();
});

test("saving settings refreshes current data and authentication", async ({ page, request }, info) => {
  const messages = info.project.name.startsWith("zh-CN") ? zh : en;
  await page.goto("/servers/1/monitoring");
  const form = page.getByTestId("monitoring-form"); await expect(form).toBeVisible();
  const input = form.locator('input[type="number"]').first();
  await input.fill("90");
  await form.getByRole("button", { name: messages.serverMonitoring.save, exact: true }).click();
  await expect.poll(async () => (await state(request)).requests.filter((row: { method: string; path: string }) => row.method === "PATCH" && row.path === "/api/v1/servers/1").length).toBe(1);
  await page.reload(); await expect(form.locator('input[type="number"]').first()).toHaveValue("90");
  expect((await state(request)).requests.filter((row: { path: string }) => row.path === "/api/v1/auth/me").length).toBeGreaterThan(1);
});


test("error retry restores fresh localized data", async ({ page, request }, info) => {
  const chinese = info.project.name.startsWith("zh-CN");
  await request.post(`${mock}/__test__/reset`, { data: { rules: { "/api/v1/servers": { body: {} } } } });
  await page.goto("/overview", { waitUntil: "commit" });
  await expect(page.getByRole("heading", { name: chinese ? "暂时无法显示此页面" : "Unable to display this page" })).toBeVisible();
  await request.post(`${mock}/__test__/reset`);
  await page.getByRole("button", { name: chinese ? "重试" : "Try again", exact: true }).click();
  await expect(page.getByTestId("overview-stats").filter({ visible: true })).toHaveCount(1);
});

test("description tasks in the global tray retain localized copy on the overview", async ({ page }, info) => {
  const messages = info.project.name.startsWith("zh-CN") ? zh : en;
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(String(error)));
  page.on("console", message => { if (message.type() === "error") errors.push(message.text()); });
  const task: DescriptionSyncJob = {
    operation_id: FIXTURE_OPERATION, status: "completed", phase: "completed",
    command: "Synchronize marketplace descriptions", framework: "counterstrikesharp",
    created_at: "2026-10-09T00:00:00Z", completed_at: "2026-10-09T00:01:00Z",
    cancel_requested: false, overwrite: true, total: 1, processed: 1,
    updated: 1, unchanged: 0, skipped: 0, failed: 0, items: [],
    message: "Fixture description task",
  };
  const inbox = { items: [], active_count: 0, running_count: 0, market_description_items: [task] };
  await page.route("**/ops-stream/operations/inbox", route => route.fulfill({
    contentType: "text/event-stream", body: `event: inbox\ndata: ${JSON.stringify(inbox)}\n\n`,
  }));
  await page.route("**/server-ops/inbox", route => route.fulfill({ json: {
    ok: true, data: {
      items: [], completedItems: [], failedItems: [], marketImportItems: [], marketDescriptionItems: [task],
      activeCount: 0, runningCount: 0, completedCount: 0, failedCount: 0,
      completedRetentionDays: 7, failedRetentionDays: 7,
    },
  } }));
  await page.goto("/overview");
  await page.getByTestId("activity-tray-toggle").click();
  const tray = page.getByTestId("activity-tray-panel");
  await expect(tray.getByRole("heading", { name: messages.plugins.sync.taskList, exact: true })).toBeVisible();
  const completed = messages.plugins.sync.taskStatus.completed;
  const taskRow = tray.getByRole("button", {
    name: `${task.framework} ${completed} ${task.message}`,
    exact: true,
  });
  await expect(taskRow.getByText(completed, { exact: true })).toBeVisible();
  await expect(tray.getByRole("status").getByText(completed, { exact: true })).toBeVisible();
  expect(errors).toEqual([]);
});

test("activity tray, upload, assistant SSE and live terminal survive navigation and cancellation", async ({ page, request }, info) => {
  const messages = info.project.name.startsWith("zh-CN") ? zh : en;
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(String(error)));
  await page.goto("/overview");
  await page.getByTestId("activity-tray-toggle").click();
  await expect(page.getByTestId("activity-tray-panel")).toBeVisible();
  await page.getByTestId("activity-tray-toggle").click();
  await page.goto("/servers/1/files");
  await expect(page.getByText("server.cfg", { exact: true })).toBeVisible();
  const upload = await page.evaluate(async () => {
    const data = new FormData(); data.append("file", new File(["uploaded fixture"], "fixture.txt"));
    const response = await fetch("/files-upload/servers/1?path=%2Fsrv%2Fcs2", { method: "POST", body: data });
    return { status: response.status, result: await response.json() };
  });
  expect(upload.status).toBe(200); expect(upload.result.success).toBe(true);
  await page.goto("/assistant?conversation=conversation-1");
  await page.locator("textarea").fill("Fixture request");
  await page.getByRole("button", { name: messages.assistant.send, exact: true }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: messages.assistant.continueWithoutServer, exact: true }).click();
  await expect(page.getByText("Fixture assistant stream", { exact: true })).toBeVisible();
  const desktop = info.project.name.endsWith("1440");
  if (!desktop) await page.getByRole("button", { name: messages.shell.openNav, exact: true }).click();
  await page.getByTestId(desktop ? "console-sidebar" : "console-mobile-drawer")
    .locator('a[data-nav-key="plugins"]').click();
  await expect(page).toHaveURL(url => url.pathname === "/plugins");
  await expect.poll(async () => (await connections(request)).activeStreamPaths[
    "/api/v1/assistant/runs/fixture-run/events"] ?? 0).toBe(0);
  await page.goto("/live-console/1?view=game");
  await expect.poll(async () => (await connections(request)).activeSockets).toBe(1);
  await expect(page.locator(".xterm-screen")).toBeVisible();
  await page.goto("/cc-navigation");
  await expect.poll(async () => {
    const state = await connections(request); return state.activeStreams + state.activeSockets;
  }).toBe(0);
  const final = await connections(request);
  expect(final.openedSockets).toBe(final.closedSockets);
  expect(errors).toEqual([]);
});
