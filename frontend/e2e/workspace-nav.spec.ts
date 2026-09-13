import { test, expect, type BrowserContext, type Page, type APIRequestContext } from "@playwright/test";
import en from "../src/i18n/messages/en-US.json" with { type: "json" };
import zh from "../src/i18n/messages/zh-CN.json" with { type: "json" };

const mock = `http://127.0.0.1:${process.env.PERF_MOCK_PORT ?? "38131"}`;

const WORKSPACE_CATEGORIES = [
  "overview",
  "operations",
  "config",
  "frameworks",
  "game-modes",
  "backups",
  "plugins",
  "plugin-configs",
  "updates",
  "maps",
  "host-config",
  "monitoring",
  "files",
  "cleanup",
  "console",
  "schedule",
  "discord",
  "help",
  "additional-fixes",
] as const;

function workspaceHref(category: (typeof WORKSPACE_CATEGORIES)[number]) {
  return category === "overview" ? "/servers/1" : `/servers/1/${category}`;
}

async function login(context: BrowserContext, actor = "admin", locale = "en-US") {
  await context.addCookies([
    { name: "upkk_access_token", value: `fixture-${actor}`, domain: "127.0.0.1", path: "/" },
    { name: "locale", value: locale, domain: "127.0.0.1", path: "/" },
  ]);
}

async function state(request: APIRequestContext) {
  return (await request.get(`${mock}/__test__/state`)).json();
}

function remoteOps(records: Array<{ path: string }>) {
  return records.filter((record) =>
    /\/(files|cleanup|console|maps)(\/|$)/.test(record.path),
  );
}

function categoryLink(page: Page, category: string) {
  return page.locator(`a[data-workspace-category="${category}"]`);
}

async function clickShowsPending(page: Page, selector: string) {
  await page.waitForFunction(
    (target) =>
      document.documentElement.getAttribute("data-link-pending-capture") ===
        "true" &&
      Boolean(
        document
          .querySelector(target)
          ?.querySelector("[data-testid='link-pending-hint']"),
      ),
    selector,
  );
  return page.evaluate(async (target) => {
    const link = document.querySelector(target);
    if (!(link instanceof HTMLAnchorElement)) {
      throw new Error(`missing ${target}`);
    }
    const hint = link.querySelector("[data-testid='link-pending-hint']");
    if (!(hint instanceof HTMLElement)) {
      throw new Error(`missing pending hint for ${target}`);
    }
    const started = performance.now();
    link.click();
    while (performance.now() - started <= 100) {
      if (hint.getAttribute("data-pending") === "true") {
        return performance.now() - started;
      }
      await new Promise((resolve) => requestAnimationFrame(resolve));
    }
    throw new Error(`pending not set within 100ms for ${target}`);
  }, selector);
}

test("all 19 workspace tabs keep the current page marker while switching", async ({
  page,
  context,
  request,
}) => {
  test.setTimeout(120_000);
  await request.post(`${mock}/__test__/reset`);
  await login(context);
  await page.goto("/servers/1/config");
  await expect(page.getByTestId("game-config-form")).toBeVisible();
  expect(WORKSPACE_CATEGORIES).toHaveLength(19);

  for (const category of WORKSPACE_CATEGORIES) {
    const link = categoryLink(page, category);
    await expect(link).toBeVisible();
    if ((await link.getAttribute("aria-current")) !== "page") {
      await link.click({ noWaitAfter: true });
    }
    await expect(page).toHaveURL((url) => url.pathname === workspaceHref(category));
    await expect(link).toHaveAttribute("aria-current", "page");
    for (const other of WORKSPACE_CATEGORIES) {
      if (other === category) continue;
      await expect(categoryLink(page, other)).not.toHaveAttribute("aria-current", "page");
    }
  }

  await categoryLink(page, "config").click();
  await expect(page.getByTestId("server-config-tabs")).toBeVisible();
  await page.getByTestId("server-config-tabs").locator('a[data-config-section="host"]').click();
  await expect(page).toHaveURL(/\/servers\/1\/host-config$/);
  await expect(page.getByTestId("host-config-form")).toBeVisible();
  await page.getByTestId("server-config-tabs").locator('a[data-config-section="game"]').click();
  await expect(page).toHaveURL(/\/servers\/1\/config$/);
  await expect(page.getByTestId("game-config-form")).toBeVisible();
});

test("unprefetched tab click feedback stays under 100ms with 3s network and backend delay", async ({
  page,
  context,
  request,
}) => {
  await login(context);
  await request.post(`${mock}/__test__/reset`, {
    data: { rules: { "/api/v1/servers/1/files": { delay: 3000 } } },
  });
  await page.goto("/servers/1/config");
  await expect(page.getByTestId("game-config-form")).toBeVisible();
  // Chrome may not throttle loopback via CDP; delay the files RSC path.
  await delayPathnames(page, ["/servers/1/files"]);
  const elapsed = await clickShowsPending(page, 'a[data-workspace-category="files"]');
  expect(elapsed).toBeLessThan(100);
  await expect(categoryLink(page, "files").locator("[data-testid='link-pending-hint']")).toHaveAttribute(
    "data-pending",
    "true",
  );
  await expect(categoryLink(page, "config")).toHaveAttribute("aria-current", "page");
  await expect(page.getByTestId("server-config-loading")).toHaveCount(0);
});

test("consecutive tab clicks move pending to the last target", async ({ page, context, request }) => {
  await login(context);
  await request.post(`${mock}/__test__/reset`, {
    data: {
      rules: {
        "/api/v1/servers/1/files": { delay: 3000 },
        "/api/v1/servers/1/console": { delay: 3000 },
      },
    },
  });
  await page.goto("/servers/1/config");
  await expect(page.getByTestId("game-config-form")).toBeVisible();
  // Chrome may not throttle loopback via CDP; delay the actual RSC paths.
  await delayPathnames(page, ["/servers/1/files", "/servers/1/console"]);
  await clickShowsPending(page, 'a[data-workspace-category="files"]');
  await clickShowsPending(page, 'a[data-workspace-category="console"]');
  await expect(categoryLink(page, "console").locator("[data-testid='link-pending-hint']")).toHaveAttribute(
    "data-pending",
    "true",
  );
  await expect(categoryLink(page, "files").locator("[data-testid='link-pending-hint']")).toHaveAttribute(
    "data-pending",
    "false",
  );
  await expect(categoryLink(page, "config")).toHaveAttribute("aria-current", "page");
});

test("back and forward restore the active workspace tab", async ({ page, context, request }) => {
  await request.post(`${mock}/__test__/reset`);
  await login(context);
  await page.goto("/servers/1/config");
  await expect(page.getByTestId("game-config-form")).toBeVisible();
  await categoryLink(page, "monitoring").click();
  await expect(page).toHaveURL(/\/servers\/1\/monitoring$/);
  await expect(page.getByTestId("monitoring-form")).toBeVisible();
  await page.goBack();
  await expect(page).toHaveURL(/\/servers\/1\/config$/);
  await expect(categoryLink(page, "config")).toHaveAttribute("aria-current", "page");
  await expect(page.getByTestId("game-config-form")).toBeVisible();
  await page.goForward();
  await expect(page).toHaveURL(/\/servers\/1\/monitoring$/);
  await expect(categoryLink(page, "monitoring")).toHaveAttribute("aria-current", "page");
});

test("monitoring streams diagnostics, A2S and logs independently", async ({ page, context, request }) => {
  await login(context);
  await request.post(`${mock}/__test__/reset`, {
    data: {
      rules: {
        "/api/v1/servers/1/plugin-diagnostics/recommendation": { gate: true },
        "/api/v1/servers/1/a2s": { gate: true },
        "/api/v1/servers/1/monitoring-logs": { gate: true },
      },
    },
  });
  await page.goto("/servers/1/monitoring", { waitUntil: "commit" });
  await expect(page.locator("h1")).toContainText("fixture-server-1");
  await expect(page.getByTestId("plugin-diagnostics-loading")).toBeVisible();
  await expect(page.getByTestId("a2s-panel-loading")).toBeVisible();
  await expect(page.getByTestId("a2s-logs-loading")).toBeVisible();
  await expect(page.getByTestId("monitoring-form")).toBeVisible();
  await expect(page.getByTestId("plugin-diagnostics")).toHaveCount(0);
  await request.post(`${mock}/__test__/release`, {
    data: { path: "/api/v1/servers/1/plugin-diagnostics/recommendation" },
  });
  await expect(page.getByTestId("plugin-diagnostics")).toBeVisible();
  await expect(page.getByTestId("a2s-logs-loading")).toBeVisible();
  await expect(page.getByTestId("a2s-panel-loading")).toBeVisible();
  await request.post(`${mock}/__test__/release`, {
    data: { path: "/api/v1/servers/1/monitoring-logs" },
  });
  await expect(page.getByTestId("a2s-logs-panel")).toBeVisible();
  await expect(page.getByTestId("a2s-panel-loading")).toBeVisible();
  await request.post(`${mock}/__test__/release`, {
    data: { path: "/api/v1/servers/1/a2s" },
  });
  await expect(page.getByTestId("a2s-panel")).toBeVisible();
});

test("workspace tabs do not prefetch SSH routes on load or hover", async ({ page, context, request }) => {
  await login(context);
  await request.post(`${mock}/__test__/reset`);
  await page.goto("/servers/1/config");
  await expect(page.getByTestId("game-config-form")).toBeVisible();
  expect(remoteOps((await state(request)).requests)).toEqual([]);
  await categoryLink(page, "files").hover();
  await categoryLink(page, "cleanup").hover();
  await categoryLink(page, "host-config").hover();
  await page.getByTestId("server-config-tabs").locator('a[data-config-section="host"]').hover();
  await expect.poll(async () => remoteOps((await state(request)).requests)).toEqual([]);
});

test("failed host and expired session stay isolated from workspace nav", async ({ page, context, request }) => {
  await login(context);
  await request.post(`${mock}/__test__/reset`, {
    data: { rules: { "/api/v1/servers/1/files": { status: 503 } } },
  });
  await page.goto("/servers/1/config");
  await categoryLink(page, "files").click();
  await expect(page).toHaveURL(/\/servers\/1\/files$/);
  await expect(page.locator("main")).toContainText("Unable to load the files workspace (503)");
  await context.clearCookies();
  await page.goto("/servers/1/monitoring");
  await expect(page).toHaveURL(/\/login(?:\?|$)/);
});

for (const locale of ["en-US", "zh-CN"] as const) {
  test(`${locale}: plugin other section uses the shared partition name and other filter`, async ({
    page,
    context,
    request,
  }) => {
    const messages = locale === "en-US" ? en : zh;
    await login(context, "admin", locale);
    await request.post(`${mock}/__test__/reset`);
    await page.goto("/plugins");
    const otherTab = page.getByTestId("market-framework-tabs").getByRole("link", {
      name: messages.plugins.frameworks.other,
      exact: true,
    });
    await expect(otherTab).toBeVisible();
    await otherTab.click();
    await expect(page).toHaveURL(/framework=other/);
    await page.getByTestId("market-create-open").click();
    await expect(page.getByTestId("market-create-framework")).toContainText(
      messages.plugins.frameworks.other,
    );
    await expect(page.locator("#market-create-category")).toContainText(
      messages.plugins.categories.other,
    );
    await expect(messages.plugins.frameworks.other).not.toBe(messages.plugins.categories.other);
    await expect(page.getByTestId("market-create-framework")).toHaveValue("counterstrikesharp");
    await expect(page.locator("#market-create-framework-hint")).toContainText(
      messages.plugins.create.frameworkHint,
    );
  });
}

const SIDEBAR_ITEMS = [
  { href: "/overview", key: "overview" },
  { href: "/servers", key: "servers" },
  { href: "/servers/initialized", key: "initializedServers" },
  { href: "/plugins", key: "plugins" },
  { href: "/assistant", key: "assistant" },
  { href: "/settings/discord", key: "discord" },
  { href: "/audit", key: "audit" },
  { href: "/settings", key: "settings" },
] as const;

function sidebarLink(page: Page, key: string) {
  return page.getByTestId("console-sidebar").locator(`a[data-nav-key="${key}"]`);
}

async function waitForHydratedOverview(page: Page) {
  await expect(page.getByTestId("overview-stats")).toBeVisible();
}

async function delayPathnames(page: Page, pathnames: readonly string[], delayMs = 3000) {
  await page.route(
    (url) => pathnames.includes(url.pathname),
    async (route) => {
      await new Promise((resolve) => setTimeout(resolve, delayMs));
      await route.continue();
    },
  );
}

test("all sidebar entries keep the current page marker while switching", async ({
  page,
  context,
  request,
}) => {
  test.setTimeout(90_000);
  await request.post(`${mock}/__test__/reset`);
  await login(context);
  await page.goto("/overview");
  await expect(page.getByTestId("console-sidebar")).toBeVisible();
  await waitForHydratedOverview(page);
  expect(SIDEBAR_ITEMS).toHaveLength(8);

  for (const item of SIDEBAR_ITEMS) {
    const link = sidebarLink(page, item.key);
    await expect(link).toBeVisible();
    if ((await link.getAttribute("aria-current")) !== "page") {
      await link.click({ noWaitAfter: true });
    }
    await expect(page).toHaveURL((url) => url.pathname === item.href);
    await expect(link).toHaveAttribute("aria-current", "page");
    for (const other of SIDEBAR_ITEMS) {
      if (other.key === item.key) continue;
      await expect(sidebarLink(page, other.key)).not.toHaveAttribute("aria-current", "page");
    }
  }

  await sidebarLink(page, "plugins").click();
  await expect(page).toHaveURL(/\/plugins$/);
  await sidebarLink(page, "home").click();
  await expect(page).toHaveURL((url) => url.pathname === "/overview");
  await expect(sidebarLink(page, "overview")).toHaveAttribute("aria-current", "page");
});

test("unprefetched sidebar click feedback stays under 100ms with 3s delay", async ({
  page,
  context,
  request,
}) => {
  await login(context);
  await request.post(`${mock}/__test__/reset`);
  await delayPathnames(page, ["/plugins"]);
  await page.goto("/overview");
  await expect(page.getByTestId("console-sidebar")).toBeVisible();
  await waitForHydratedOverview(page);
  const elapsed = await clickShowsPending(
    page,
    '[data-testid="console-sidebar"] a[data-nav-key="plugins"]',
  );
  expect(elapsed).toBeLessThan(100);
  await expect(sidebarLink(page, "plugins").locator("[data-testid='link-pending-hint']")).toHaveAttribute(
    "data-pending",
    "true",
  );
  await expect(sidebarLink(page, "overview")).toHaveAttribute("aria-current", "page");
});

test("consecutive sidebar clicks move pending to the last target", async ({
  page,
  context,
  request,
}) => {
  await login(context);
  await request.post(`${mock}/__test__/reset`);
  await delayPathnames(page, ["/plugins", "/servers"]);
  await page.goto("/overview");
  await expect(page.getByTestId("console-sidebar")).toBeVisible();
  await waitForHydratedOverview(page);
  await clickShowsPending(page, '[data-testid="console-sidebar"] a[data-nav-key="plugins"]');
  await clickShowsPending(page, '[data-testid="console-sidebar"] a[data-nav-key="servers"]');
  await expect(sidebarLink(page, "servers").locator("[data-testid='link-pending-hint']")).toHaveAttribute(
    "data-pending",
    "true",
  );
  await expect(sidebarLink(page, "plugins").locator("[data-testid='link-pending-hint']")).toHaveAttribute(
    "data-pending",
    "false",
  );
  await expect(sidebarLink(page, "overview")).toHaveAttribute("aria-current", "page");
});

test("mobile drawer uses the same pending nav links", async ({ page, context, request }) => {
  await login(context);
  await request.post(`${mock}/__test__/reset`);
  await page.setViewportSize({ width: 390, height: 900 });
  await delayPathnames(page, ["/plugins"]);
  await page.goto("/overview");
  await waitForHydratedOverview(page);
  await page.getByTestId("console-mobile-open").click();
  const drawer = page.getByTestId("console-mobile-drawer");
  await expect(drawer).toBeVisible();
  for (const item of SIDEBAR_ITEMS) {
    await expect(drawer.locator(`a[data-nav-key="${item.key}"]`)).toBeVisible();
  }
  const elapsed = await clickShowsPending(
    page,
    '[data-testid="console-mobile-drawer"] a[data-nav-key="plugins"]',
  );
  expect(elapsed).toBeLessThan(100);
  await expect(drawer.locator('a[data-nav-key="plugins"] [data-testid="link-pending-hint"]')).toHaveAttribute(
    "data-pending",
    "true",
  );
});

