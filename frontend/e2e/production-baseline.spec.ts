import { mkdir, writeFile } from "node:fs/promises";
import { test, expect, type BrowserContext, type Page, type Response } from "@playwright/test";
import en from "../src/i18n/messages/en-US.json" with { type: "json" };
import zh from "../src/i18n/messages/zh-CN.json" with { type: "json" };
import { protocolFromEnv, summarize, type ProductionRouteMetrics } from "./helpers/production-metrics";

const mock = `http://127.0.0.1:${process.env.PERF_MOCK_PORT ?? "38151"}`;

async function login(context: BrowserContext, actor: string | null, locale: string) {
  const cookies = [{ name: "locale", value: locale, domain: "127.0.0.1", path: "/" }];
  if (actor) {
    cookies.push({
      name: "upkk_access_token",
      value: `fixture-${actor}`,
      domain: "127.0.0.1",
      path: "/",
    });
  }
  await context.addCookies(cookies);
}

function attachObservers(page: Page) {
  return page.addInitScript(() => {
    const state = { cls: 0, lcp: 0, inp: 0, longTasks: 0, longTaskMs: 0 };
    (window as Window & { __perfBaseline?: typeof state }).__perfBaseline = state;
    new PerformanceObserver((list) => {
      for (const entry of list.getEntries()) {
        const shift = entry as PerformanceEntry & { hadRecentInput?: boolean; value: number };
        if (!shift.hadRecentInput) state.cls += shift.value;
      }
    }).observe({ type: "layout-shift", buffered: true });
    new PerformanceObserver((list) => {
      for (const entry of list.getEntries()) state.lcp = entry.startTime;
    }).observe({ type: "largest-contentful-paint", buffered: true });
    new PerformanceObserver((list) => {
      for (const entry of list.getEntries()) state.inp = Math.max(state.inp, entry.duration);
    }).observe({ type: "event", buffered: true, durationThreshold: 0 } as PerformanceObserverInit);
    new PerformanceObserver((list) => {
      for (const entry of list.getEntries()) {
        state.longTasks += 1;
        state.longTaskMs += entry.duration;
      }
    }).observe({ type: "longtask", buffered: true });
  });
}

function trackPayloads(page: Page) {
  let htmlBytes = 0;
  let rscBytes = 0;
  const pending: Promise<void>[] = [];
  page.on("response", (response: Response) => {
    pending.push(
      response
        .body()
        .then((body) => {
          const type = response.headers()["content-type"] ?? "";
          const url = response.url();
          if (type.includes("text/html")) htmlBytes = body.byteLength;
          if (type.includes("text/x-component") || url.includes("_rsc")) {
            rscBytes += body.byteLength;
          }
        })
        .catch(() => undefined),
    );
  });
  return async () => {
    await Promise.all(pending);
    return { htmlBytes, rscBytes };
  };
}

async function oneVisit(
  context: BrowserContext,
  route: "/login" | "/overview",
  locale: "en-US" | "zh-CN",
  actor: string | null,
): Promise<ProductionRouteMetrics> {
  await login(context, actor, locale);
  const page = await context.newPage();
  try {
    await attachObservers(page);
    const payloads = trackPayloads(page);
    const started = Date.now();
    await page.goto(route, { waitUntil: "domcontentloaded" });
    if (route === "/login") {
      const submit = locale === "en-US" ? en.login.submit : zh.login.submit;
      await expect(page.getByRole("button", { name: submit, exact: true })).toBeVisible();
    } else {
      await expect(page.getByTestId("overview-stats")).toBeVisible();
    }
    const critical = Date.now() - started;
    await page.waitForLoadState("load");
    const sizes = await payloads();
    return page.evaluate(
      ({ routeName, localeName, htmlBytes, rscBytes, criticalMs }) => {
        const nav = performance.getEntriesByType("navigation")[0] as PerformanceNavigationTiming | undefined;
        const paints = performance.getEntriesByType("paint");
        const fcp = paints.find((entry) => entry.name === "first-contentful-paint");
        const resources = performance.getEntriesByType("resource") as PerformanceResourceTiming[];
        const js = resources.filter((entry) => {
          try {
            return new URL(entry.name).pathname.includes("/_next/static/") && entry.name.endsWith(".js");
          } catch {
            return false;
          }
        });
        const observed = (window as Window & {
          __perfBaseline?: { cls: number; lcp: number; inp: number; longTasks: number; longTaskMs: number };
        }).__perfBaseline;
        return {
          route: routeName,
          locale: localeName,
          ttfb_ms: nav ? Math.round((nav.responseStart - nav.requestStart) * 10) / 10 : null,
          fcp_ms: fcp ? Math.round(fcp.startTime * 10) / 10 : null,
          lcp_ms: observed?.lcp ? Math.round(observed.lcp * 10) / 10 : null,
          cls: observed ? Math.round(observed.cls * 1000) / 1000 : null,
          inp_ms: observed?.inp ? Math.round(observed.inp * 10) / 10 : null,
          long_task_count: observed?.longTasks ?? 0,
          long_task_total_ms: Math.round((observed?.longTaskMs ?? 0) * 10) / 10,
          html_bytes: htmlBytes || null,
          rsc_bytes: rscBytes,
          js_transfer_bytes: js.reduce((total, entry) => total + (entry.transferSize || 0), 0),
          critical_content_ms: criticalMs,
        };
      },
      {
        routeName: route,
        localeName: locale,
        htmlBytes: sizes.htmlBytes,
        rscBytes: sizes.rscBytes,
        criticalMs: critical,
      },
    );
  } finally {
    await page.close();
  }
}

for (const locale of ["en-US", "zh-CN"] as const) {
  for (const route of ["/login", "/overview"] as const) {
    test(`production baseline ${locale} ${route}`, async ({ context }) => {
      const protocol = protocolFromEnv();
      const actor = route === "/login" ? null : "admin";
      for (let round = 0; round < protocol.rounds; round += 1) {
        for (let index = 0; index < protocol.warmup; index += 1) {
          await oneVisit(context, route, locale, actor);
        }
        const samples: ProductionRouteMetrics[] = [];
        for (let index = 0; index < protocol.measure; index += 1) {
          samples.push(await oneVisit(context, route, locale, actor));
        }
        const result = {
          claimed_gains: false,
          locale,
          route,
          round: round + 1,
          protocol,
          summary: summarize(samples),
          samples,
        };
        await mkdir("test-results/perf-baseline", { recursive: true });
        const file = `test-results/perf-baseline/${locale}${route.replaceAll("/", "_")}-r${round + 1}.json`;
        await writeFile(file, JSON.stringify(result, null, 2));
        expect(samples.length).toBe(protocol.measure);
      }
    });
  }
}

test("mock health is reachable", async ({ request }) => {
  const response = await request.get(`${mock}/health`);
  expect(response.ok()).toBeTruthy();
});
