import { mkdir, writeFile } from "node:fs/promises";
import { gzipSync } from "node:zlib";
import { test, expect, type BrowserContext, type Page, type Response } from "@playwright/test";
import en from "../src/i18n/messages/en-US.json" with { type: "json" };
import zh from "../src/i18n/messages/zh-CN.json" with { type: "json" };
import { protocolFromEnv, summarize, type ProductionRouteMetrics } from "./helpers/production-metrics";
import {
  PRODUCTION_HEIGHT,
  surfacesFromEnv,
  widthsFromEnv,
  type ProductionSurface,
} from "./helpers/production-surfaces";

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

function encodedBytes(response: Response): number | null {
  const encoding = response.headers()["content-encoding"] ?? "";
  const length = Number(response.headers()["content-length"]);
  if (!encoding.includes("gzip") && !encoding.includes("br") && !encoding.includes("deflate")) {
    return null;
  }
  return Number.isFinite(length) && length > 0 ? length : null;
}

function trackPayloads(page: Page) {
  let htmlBytes = 0;
  let htmlGzipBytes: number | null = null;
  let rscBytes = 0;
  let rscGzipBytes = 0;
  const pending: Promise<void>[] = [];
  page.on("response", (response: Response) => {
    const type = response.headers()["content-type"] ?? "";
    const url = response.url();
    const isHtml = type.includes("text/html");
    const isRsc = type.includes("text/x-component") || url.includes("_rsc");
    if (!isHtml && !isRsc) return;
    if (type.includes("text/event-stream")) return;
    pending.push(
      response
        .body()
        .then((body) => {
          const compressed = encodedBytes(response) ?? gzipSync(body).byteLength;
          if (isHtml) {
            htmlBytes = body.byteLength;
            htmlGzipBytes = compressed;
          }
          if (isRsc) {
            rscBytes += body.byteLength;
            rscGzipBytes += compressed;
          }
        })
        .catch(() => undefined),
    );
  });
  return async () => {
    await Promise.all(pending);
    return { htmlBytes, htmlGzipBytes, rscBytes, rscGzipBytes: rscGzipBytes || null };
  };
}

async function waitForCritical(
  page: Page,
  surface: ProductionSurface,
  locale: "en-US" | "zh-CN",
) {
  if (surface.name === "login") {
    const submit = locale === "en-US" ? en.login.submit : zh.login.submit;
    await expect(page.getByRole("button", { name: submit, exact: true })).toBeVisible();
    return;
  }
  if (surface.name === "overview") {
    await expect(page.getByTestId("overview-stats")).toBeVisible();
    return;
  }
  if (surface.name === "servers") {
    await expect(page.getByText("fixture-server-1", { exact: true }).first()).toBeVisible();
    return;
  }
  if (surface.name === "activity-tray") {
    await expect(page.getByTestId("activity-tray-toggle")).toBeVisible();
    await page.getByTestId("activity-tray-toggle").click();
    await expect(page.getByTestId("activity-tray-panel")).toBeVisible();
    return;
  }
  if (surface.name === "plugins-install") {
    await expect(page.getByText("Fixture Plugin", { exact: true }).first()).toBeVisible();
    await expect(page.getByTestId("market-install-form")).toBeVisible();
    return;
  }
  if (surface.name === "assistant") {
    await expect(page.getByText("Fixture conversation", { exact: true }).first()).toBeVisible();
    return;
  }
  await expect(page.getByTestId("files-dropzone")).toBeVisible();
}

async function oneVisit(
  context: BrowserContext,
  surface: ProductionSurface,
  locale: "en-US" | "zh-CN",
  width: number,
): Promise<ProductionRouteMetrics> {
  await login(context, surface.actor, locale);
  const page = await context.newPage();
  try {
    await page.setViewportSize({ width, height: PRODUCTION_HEIGHT });
    await attachObservers(page);
    const payloads = trackPayloads(page);
    const started = Date.now();
    await page.goto(surface.path, { waitUntil: "domcontentloaded" });
    await waitForCritical(page, surface, locale);
    const critical = Date.now() - started;
    // Do not wait for `load`: the activity-tray EventSource stays open and
    // can keep the load watcher or a naive `response.body()` hang.
    const sizes = await payloads();
    // Await before `finally` closes the page. A bare `return page.evaluate(...)`
    // schedules the close while the CDP call is still in flight.
    const metrics = await page.evaluate(
      ({ routeName, localeName, htmlBytes, htmlGzipBytes, rscBytes, rscGzipBytes, criticalMs }) => {
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
          html_gzip_bytes: htmlGzipBytes,
          rsc_bytes: rscBytes,
          rsc_gzip_bytes: rscGzipBytes,
          js_transfer_bytes: js.reduce((total, entry) => total + (entry.transferSize || 0), 0),
          critical_content_ms: criticalMs,
        };
      },
      {
        routeName: surface.name,
        localeName: locale,
        htmlBytes: sizes.htmlBytes,
        htmlGzipBytes: sizes.htmlGzipBytes,
        rscBytes: sizes.rscBytes,
        rscGzipBytes: sizes.rscGzipBytes,
        criticalMs: critical,
      },
    );
    return metrics;
  } finally {
    await page.close();
  }
}

for (const locale of ["en-US", "zh-CN"] as const) {
  for (const surface of surfacesFromEnv()) {
    for (const width of widthsFromEnv()) {
      test(`production baseline ${locale} ${surface.name} ${width}`, async ({ context }) => {
        const protocol = protocolFromEnv();
        for (let round = 0; round < protocol.rounds; round += 1) {
          for (let index = 0; index < protocol.warmup; index += 1) {
            await oneVisit(context, surface, locale, width);
          }
          const samples: ProductionRouteMetrics[] = [];
          for (let index = 0; index < protocol.measure; index += 1) {
            samples.push(await oneVisit(context, surface, locale, width));
          }
          const result = {
            claimed_gains: false,
            locale,
            route: surface.name,
            path: surface.path,
            width,
            round: round + 1,
            protocol,
            summary: summarize(samples),
            samples,
          };
          await mkdir("test-results/perf-baseline", { recursive: true });
          const file = `test-results/perf-baseline/${locale}_${surface.name}_${width}-r${round + 1}.json`;
          await writeFile(file, JSON.stringify(result, null, 2));
          expect(samples.length).toBe(protocol.measure);
        }
      });
    }
  }
}

test("mock health is reachable", async ({ request }) => {
  const response = await request.get(`${mock}/health`);
  expect(response.ok()).toBeTruthy();
});
