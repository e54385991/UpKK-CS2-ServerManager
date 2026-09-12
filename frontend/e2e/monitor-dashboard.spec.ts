import { expect, test, type BrowserContext, type APIRequestContext, type Page } from "@playwright/test";

const mock = `http://127.0.0.1:${process.env.MONITOR_MOCK_PORT ?? "38141"}`;

async function login(context: BrowserContext, locale = "zh-CN") {
  await context.addCookies([
    { name: "upkk_access_token", value: "fixture-admin", domain: "127.0.0.1", path: "/" },
    { name: "locale", value: locale, domain: "127.0.0.1", path: "/" },
  ]);
}

async function setScenario(request: APIRequestContext, name: string) {
  await request.get(`${mock}/__test__/scenario?name=${name}`);
}

async function openSettings(page: Page) {
  await page.goto("/settings");
  await expect(page.getByTestId("settings-section-performance")).toBeVisible();
  await expect(page.getByTestId("monitor-status")).not.toContainText(/正在加载|Loading monitoring/, {
    timeout: 20_000,
  });
}

test.beforeEach(async ({ request }) => {
  await setScenario(request, "healthy");
});

test("settings opens on system monitoring with charts", async ({ page, context }) => {
  await login(context);
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await openSettings(page);
  await expect(page.getByTestId("monitor-status")).toContainText(/运行正常|Healthy/);
  await expect(page.getByTestId("monitor-chart-latency")).toBeVisible();
  await expect(page.getByTestId("monitor-range-1h")).toBeVisible();
  await expect(page.getByRole("switch", { name: /后台采集|Background collection/ })).toHaveAttribute("aria-checked", "true");
  expect(errors).toEqual([]);
});

test("latency spikes surface critical alerts and error groups", async ({ page, context, request }) => {
  await setScenario(request, "spike");
  await login(context);
  await openSettings(page);
  await expect(page.getByTestId("monitor-alerts")).toContainText(/P95|延迟/);
  await expect(page.getByTestId("monitor-alerts")).toContainText("1200");
  await expect(page.getByTestId("monitor-alerts")).not.toContainText("Request p95 reached");
  await page.getByTestId("monitor-view-requests").click();
  await expect(page.getByText("GET /api/v1/servers")).toBeVisible();
  await expect(page.getByTestId("monitor-error-group")).toBeVisible();
  await expect(page.getByTestId("monitor-error-details")).toContainText("fixture failure");
  await page.getByTestId("monitor-error-source").selectOption("request");
  await expect(page.getByTestId("monitor-error-details")).toContainText("fixture failure");
  await page.getByTestId("monitor-error-group").first().click();
  await expect(page.getByTestId("monitor-focus")).toBeVisible();
  await page.getByTestId("monitor-clear-focus").click();
  await expect(page.getByTestId("monitor-focus")).toHaveCount(0);
  await expect(page.getByTestId("monitor-dropped")).toContainText("4");
  await page.getByTestId("monitor-error-more").click();
  await expect(page.getByTestId("monitor-error-details")).toContainText(/fixture failure later/);
});

test("redis disconnect is a critical state, not healthy", async ({ page, context, request }) => {
  await setScenario(request, "redis-down");
  await login(context);
  await openSettings(page);
  await expect(page.getByTestId("monitor-status")).toContainText(/严重|Critical/);
  await expect(page.getByTestId("monitor-alerts")).toContainText(/Redis/);
});

test("empty history is collecting or unavailable, never healthy", async ({ page, context, request }) => {
  await setScenario(request, "empty");
  await login(context);
  await openSettings(page);
  await expect(page.getByTestId("monitor-status")).toContainText(/采集|不可用|Collecting|unavailable|History storage/);
  await expect(page.getByTestId("monitor-status")).not.toContainText(/运行正常|Healthy/);
});

test("global switch stops collection and page polling", async ({ page, context, request }) => {
  await login(context);
  await openSettings(page);
  await page.getByRole("switch", { name: /后台采集|Background collection/ }).click();
  await expect(page.getByTestId("monitor-status")).toContainText(/监控已关闭|Monitoring is off/);
  const before = await (await request.get(`${mock}/__test__/state`)).json();
  await page.waitForTimeout(11_000);
  const after = await (await request.get(`${mock}/__test__/state`)).json();
  expect(after.monitorGets - before.monitorGets).toBeLessThanOrEqual(1);
  expect(after.enabled).toBe(false);
});

test("pause, range, and export stay on the dashboard", async ({ page, context, request }) => {
  await login(context);
  await openSettings(page);
  await page.getByRole("button", { name: /暂停刷新|Pause refresh/ }).click();
  await expect(page.getByRole("button", { name: /恢复刷新|Resume refresh/ })).toBeVisible();
  await page.getByTestId("monitor-range-24h").click();
  await expect.poll(async () => (await (await request.get(`${mock}/__test__/state`)).json()).lastRange).toBe("24h");
  const [download] = await Promise.all([
    page.waitForEvent("download"),
    page.getByRole("button", { name: /导出诊断包|Export diagnostics/ }).click(),
  ]);
  expect(download.suggestedFilename()).toMatch(/cs2-panel-monitor-24h/);
});

test("english copy and mobile layout stay single column", async ({ page, context }) => {
  await login(context, "en-US");
  await page.setViewportSize({ width: 390, height: 844 });
  await openSettings(page);
  await expect(page.getByRole("heading", { name: "System monitoring" })).toBeVisible();
  await expect(page.getByTestId("monitor-chart-latency")).toBeVisible();
  await expect(page.getByRole("button", { name: "1 hour" })).toBeVisible();
});
