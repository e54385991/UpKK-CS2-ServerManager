import { test, expect, type BrowserContext } from "@playwright/test";
import { instant } from "@next/playwright";
import { CACHE_COMPONENTS_SURFACES } from "./helpers/cache-components-surfaces.mjs";
import en from "../src/i18n/messages/en-US.json" with { type: "json" };
import zh from "../src/i18n/messages/zh-CN.json" with { type: "json" };
import { expectPageContent } from "./helpers/cache-components-content";

const publicPaths = ["/login", "/register", "/forgot-password", "/reset-password", "/google-callback", "/deployment-tutorial"];
async function session(context: BrowserContext, locale: string, anonymous: boolean) {
  await context.clearCookies();
  await context.addCookies([
    { name: "locale", value: locale, url: "http://127.0.0.1:31961" },
    ...anonymous ? [] : [{ name: "upkk_access_token", value: "fixture-admin-a", httpOnly: true, url: "http://127.0.0.1:31961" }],
  ]);
}
function marker(path: string) {
  const pathname = path.split("?")[0]!;
  if (pathname === "/") return 'section[data-route="/overview"]';
  if (publicPaths.includes(pathname) && pathname !== "/deployment-tutorial" && pathname !== "/google-callback") return '[data-testid="public-auth-frame"]';
  if (pathname === "/deployment-tutorial") return '[data-testid="tutorial-guide"]';
  if (pathname === "/google-callback") return '[data-testid="google-callback-shell"]';
  return `section[data-route="${pathname.replace(/\/servers\/\d+/, "/servers/[id]").replace(/\/plugins\/\d+/, "/plugins/[id]").replace(/\/live-console\/\d+/, "/live-console/[id]")}"]`;
}

for (const path of CACHE_COMPONENTS_SURFACES) {
  for (const navigation of ["initial", "link"] as const) {
    test(`${navigation}: ${path} exposes a meaningful localized shell`, async ({ page, context, baseURL }, info) => {
      const locale = info.project.name.startsWith("zh-CN") ? "zh-CN" : "en-US";
      const anonymous = publicPaths.some(prefix => path.startsWith(prefix));
      await session(context, locale, anonymous);
      const errors: string[] = [];
      page.on("pageerror", error => errors.push(String(error)));
      if (navigation === "link") {
        await page.goto("/cc-navigation");
        await expect(page.getByTestId(`navigate:${path}`)).toBeVisible();
      }
      await instant(page, async () => {
        if (navigation === "initial") await page.goto(path);
        else await page.getByTestId(`navigate:${path}`).click();
        const expectedPath = path === "/" ? "/overview" : path.split("?")[0];
        await page.waitForURL(url => url.pathname === expectedPath);
        const shell = page.locator(marker(path));
        await expect(shell).toBeVisible();
        await expect(shell.locator("h1, h2").first()).toBeVisible();
        await expect(page.locator("html")).toHaveAttribute("lang", locale);
        await expect(page.getByText(/fixture-(admin|member)-[ab]/)).toHaveCount(0);
        if (!anonymous) await expect(page.getByTestId("overview-stats")).toHaveCount(0);
        const catalog = locale === "en-US" ? en : zh;
        if (path.startsWith("/servers/1")) await expect(page.getByRole("heading", { name: catalog.serverDetail.workspaceTitle, exact: true })).toBeVisible();
      }, { baseURL });
      // Initial locked documents do not resume; request an unlocked document to verify content.
      if (navigation === "initial") await page.reload();
      await expect(page.locator(marker(path))).toBeVisible();
      await expectPageContent(page, path, locale);
      expect(errors).toEqual([]);
    });
  }
}

test("the test lock gates real business data and releases on a Link navigation", async ({ page, context }) => {
  await session(context, "en-US", false);
  await page.goto("/cc-navigation");
  await instant(page, async () => {
    await page.getByTestId("navigate:/overview").click();
    await page.waitForURL("**/overview");
    await expect(page.locator('section[data-route="/overview"]')).toBeVisible();
    await expect(page.getByTestId("overview-stats")).toHaveCount(0);
  });
  await expect(page.getByTestId("overview-stats")).toBeVisible();
});
