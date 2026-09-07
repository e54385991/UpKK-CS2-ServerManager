import { test, expect } from "@playwright/test";

for (const locale of ["zh-CN", "en-US"] as const) {
  test(`${locale}: release credentials guidance and profile navigation`, async ({ page, context }) => {
    await context.addCookies([
      { name: "upkk_access_token", value: "isolated-test-session", domain: "localhost", path: "/" },
      { name: "locale", value: locale, domain: "localhost", path: "/" },
    ]);
    const label = locale === "zh-CN" ? "前往个人中心设置" : "Open profile settings";
    const guidance = locale === "zh-CN" ? "GitHub 身份验证失败" : "GitHub authentication failed";
    await page.goto("/plugins/1?serverId=1");
    const market = page.getByTestId("market-install-form");
    await expect(market.getByRole("alert")).toContainText(guidance);
    await expect(market.getByRole("alert")).not.toContainText("Bad credentials");
    await market.getByRole("link", { name: label }).click();
    await expect(page).toHaveURL(/\/settings\/profile#profile-github-token$/);
    await expect(page.locator("#profile-github-token")).toBeVisible();

    await page.goto("/plugins");
    await page.getByRole("button", { name: locale === "zh-CN" ? "从 GitHub 安装" : "Install from GitHub", exact: true }).click();
    const dialog = page.getByRole("dialog");
    await dialog.locator("#github-repo").fill("https://github.com/example/plugin");
    const fetchButton = dialog.getByRole("button", { name: locale === "zh-CN" ? "获取发行版" : "Fetch releases", exact: true });
    await fetchButton.click();
    await expect(dialog.getByRole("alert")).toContainText(guidance);
    await expect(dialog.getByRole("link", { name: label })).toHaveAttribute("href", "/settings/profile#profile-github-token");
    await dialog.locator("#github-repo").fill("https://github.com/example/missing");
    await fetchButton.click();
    await expect(dialog.getByRole("alert")).toContainText("HTTP 404: Not Found");
    await expect(dialog.getByRole("link", { name: label })).toHaveCount(0);
  });
}
