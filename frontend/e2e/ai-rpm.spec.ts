import { test, expect } from "@playwright/test";

for (const locale of ["zh-CN", "en-US"] as const) {
  test(`${locale}: AI RPM saves and survives reload`, async ({ page, context }) => {
    await context.addCookies([
      { name: "upkk_access_token", value: "isolated-test-session", domain: "localhost", path: "/" },
      { name: "locale", value: locale, domain: "localhost", path: "/" },
    ]);
    await page.setViewportSize({ width: locale === "zh-CN" ? 390 : 1440, height: 900 });
    await page.goto("/settings");
    const card = page.getByTestId("ai-settings-card");
    await card.locator("summary").click();
    const rpm = card.getByLabel(locale === "zh-CN" ? "每分钟最大请求数（RPM）" : "Maximum requests per minute (RPM)");
    await expect(rpm).toHaveAttribute("min", "1");
    await expect(rpm).toHaveAttribute("max", "10000");
    await rpm.fill("17");
    const request = page.waitForRequest(request => request.url().includes("/ai-settings") && request.method() === "PUT");
    await card.getByTestId("ai-settings-save").click();
    expect((await request).postDataJSON().requests_per_minute).toBe(17);
    await expect(card.getByTestId("ai-settings-banner")).toContainText(locale === "zh-CN" ? "已保存" : "saved");
    await page.reload();
    await card.locator("summary").click();
    await expect(rpm).toHaveValue("17");
    await rpm.scrollIntoViewIfNeeded();
    await page.screenshot({ path: `/tmp/ai-rpm-${locale}.png`, fullPage: true });
    expect(await card.evaluate(el => el.scrollWidth <= el.clientWidth + 1)).toBeTruthy();
    await rpm.fill("0");
    await card.getByTestId("ai-settings-save").click();
    await expect(card.getByTestId("ai-settings-banner")).toContainText("RPM must be between 1 and 10000");
  });
}
