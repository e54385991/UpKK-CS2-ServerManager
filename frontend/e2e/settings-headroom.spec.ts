import { expect, test } from "@playwright/test";

const mock = `http://127.0.0.1:${process.env.MONITOR_MOCK_PORT ?? "38141"}`;

for (const locale of ["en-US", "zh-CN"]) {
  for (const width of [390, 1440]) {
    test(`${locale} ${width}: settings lazy sections keep drafts and their mounted state`, async ({ page, context, request }) => {
      await request.get(`${mock}/__test__/scenario?name=healthy`);
      await context.addCookies([
        { name: "upkk_access_token", value: "fixture-admin", domain: "127.0.0.1", path: "/" },
        { name: "locale", value: locale, domain: "127.0.0.1", path: "/" },
      ]);
      await page.setViewportSize({ width, height: 844 });
      const errors: string[] = [];
      page.on("pageerror", (error) => errors.push(error.message));
      await page.goto("/settings");
      const nav = page.getByTestId("settings-category-nav");
      await nav.locator('a[href="#settings-downloads"]').click();
      await expect(page.getByTestId("settings-section-downloads")).toBeVisible();
      await page.locator("#proxy-mode").selectOption("github_url");
      const proxy = page.locator("#github-proxy-url");
      await proxy.fill("https://draft.invalid/");
      await nav.locator('a[href="#settings-ai"]').click();
      const ai = page.getByTestId("ai-settings-card");
      await expect(ai).toBeVisible();
      await page.locator("#ai-model").fill("draft-model");
      await ai.evaluate((element) => element.setAttribute("data-kept-mount", "yes"));
      await nav.locator('a[href="#settings-transfer"]').click();
      await expect(page.getByTestId("settings-transfer-card")).toBeVisible();
      await expect(ai).toBeHidden();
      await nav.locator('a[href="#settings-ai"]').click();
      await expect(page.locator("#ai-model")).toHaveValue("draft-model");
      await expect(ai).toHaveAttribute("data-kept-mount", "yes");
      await nav.locator('a[href="#settings-downloads"]').click();
      await expect(proxy).toHaveValue("https://draft.invalid/");
      await expect(page.locator("html")).toHaveAttribute("lang", locale);
      expect(errors).toEqual([]);
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    });
  }
}

for (const locale of ["en-US", "zh-CN"]) {
  test(`${locale}: a direct settings section link hydrates without replacing the page`, async ({ page, context }) => {
    await context.addCookies([
      { name: "upkk_access_token", value: "fixture-admin", domain: "127.0.0.1", path: "/" },
      { name: "locale", value: locale, domain: "127.0.0.1", path: "/" },
    ]);
    const errors: string[] = [];
    page.on("pageerror", (error) => errors.push(error.message));
    page.on("console", (message) => {
      if (message.type() === "error" && /hydration|Minified React error/.test(message.text())) {
        errors.push(message.text());
      }
    });
    await page.goto("/settings#settings-ai");
    await expect(page.getByTestId("ai-settings-card")).toBeVisible();
    await expect(page.getByTestId("settings-category-nav").locator('a[href="#settings-ai"]')).toHaveAttribute("aria-current", "page");
    expect(errors).toEqual([]);
  });
}
