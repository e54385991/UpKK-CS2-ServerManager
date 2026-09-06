import { test, expect } from "@playwright/test";

const mockUrl = `http://127.0.0.1:${process.env.OVERVIEW_MOCK_PORT ?? "38121"}`;

for (const locale of ["zh-CN", "en-US"] as const) {
  for (const width of [390, 1440]) {
    for (const navigation of ["initial", "client"] as const) {
      for (const outcome of ["success", "failure"] as const) {
        test(`${locale} ${width} ${navigation} ${outcome}: counters precede host probe`, async ({ page, context, request }) => {
          await request.post(`${mockUrl}/__test__/reset`);
          await context.addCookies([
            { name: "upkk_access_token", value: "isolated-fixture-session", domain: "127.0.0.1", path: "/" },
            { name: "locale", value: locale, domain: "127.0.0.1", path: "/" },
          ]);
          await page.setViewportSize({ width, height: 900 });
          const errors: string[] = [];
          page.on("pageerror", error => errors.push(error.message));
          if (navigation === "client") {
            await page.goto("/deployment-tutorial");
            await page.evaluate(() => {
              (window as Window & { navigationMarker?: string }).navigationMarker = "same-document";
            });
            await page.getByRole("link", {
              name: locale === "zh-CN" ? "返回总览" : "Back to overview", exact: true,
            }).click();
          } else {
            await page.goto("/overview", { waitUntil: "commit" });
          }
          // The backend gate remains closed until these assertions pass.
          // A shared Promise.all would keep the counters hidden indefinitely.
          await expect(page.getByTestId("overview-stats")).toBeVisible();
          await expect(page.getByTestId("overview-stats")).toContainText(locale === "zh-CN" ? "服务器总数" : "Total servers");
          await expect(page.getByTestId("overview-host-loading")).toBeVisible();
          await expect.poll(async () => (await (await request.get(`${mockUrl}/__test__/state`)).json()).hostRequests).toBeGreaterThan(0);
          await expect(page.getByTestId("overview-host-info")).toHaveCount(0);
          if (navigation === "client") {
            expect(await page.evaluate(() => (window as Window & { navigationMarker?: string }).navigationMarker)).toBe("same-document");
          }
          await request.post(`${mockUrl}/__test__/release?outcome=${outcome}`);
          await expect(page.getByTestId("overview-host-loading")).toHaveCount(0);
          await expect(page.getByTestId("overview-stats")).toBeVisible();
          if (outcome === "success") {
            const section = page.getByTestId("overview-host-info");
            await expect(section).toBeVisible();
            for (const id of [1, 2]) {
              const card = section.locator("div.rounded-lg").filter({ hasText: `fixture-cpu-${id}` }).last();
              await expect(card).toContainText(`fixture-server-${id}`);
            }
          } else {
            await expect(page.getByTestId("overview-host-info")).toHaveCount(0);
          }
          expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy();
          expect(errors).toEqual([]);
        });
      }
    }
  }
}

test("Next runtime reports no compilation or execution errors", async ({ page, context, request }) => {
  await request.post(`${mockUrl}/__test__/reset`);
  await context.addCookies([{ name: "upkk_access_token", value: "isolated-fixture-session", domain: "127.0.0.1", path: "/" }]);
  await page.goto("/overview", { waitUntil: "commit" });
  await expect(page.getByTestId("overview-stats")).toBeVisible();
  await request.post(`${mockUrl}/__test__/release?outcome=empty`);
  await expect(page.getByTestId("overview-host-loading")).toHaveCount(0);
  const rpc = async (method: string, params: Record<string, unknown> = {}) => {
    const response = await page.request.post("/_next/mcp", {
      headers: { accept: "application/json, text/event-stream" },
      data: { jsonrpc: "2.0", id: 1, method, params },
    });
    const body = await response.text();
    const line = body.split("\n").find(line => line.startsWith("data: "));
    return JSON.parse(line ? line.slice(6) : body);
  };
  const tools = await rpc("tools/list");
  const names = tools.result.tools.map((tool: { name: string }) => tool.name);
  for (const name of ["get_compilation_issues", "get_errors"]) {
    expect(names).toContain(name);
    const result = await rpc("tools/call", { name, arguments: {} });
    expect(result.error).toBeUndefined();
    expect(result.result.isError).not.toBe(true);
    await test.info().attach(name, { body: JSON.stringify(result), contentType: "application/json" });
    const diagnostics = JSON.parse(result.result.content[0].text);
    expect(diagnostics).toEqual(name === "get_compilation_issues"
      ? { issues: [] }
      : { configErrors: [], sessionErrors: [] });
  }
});
