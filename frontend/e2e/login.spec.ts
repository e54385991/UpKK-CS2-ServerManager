import { expect, test } from "@playwright/test";
import { loginViaUi } from "./helpers/auth";
import { stubCaptcha } from "./helpers/captcha";

test("login page renders captcha and sign-in controls", async ({ page }) => {
  await page.goto("/login");
  await expect(page.getByLabel(/用户名|Username/i)).toBeVisible();
  await expect(page.getByLabel(/密码|Password/i)).toBeVisible();
  await expect(page.getByRole("button", { name: /登录|Sign in/i })).toBeVisible();
  await expect(page.getByAltText(/验证码|Captcha/i)).toBeVisible();
  await expect(page.getByRole("link", { name: /忘记密码|Forgot password/i })).toBeVisible();
  await expect(page.getByRole("link", { name: /去注册|Create one/i })).toBeVisible();
  await expect(page.locator("[data-google-oauth]")).toHaveAttribute(
    "data-google-oauth",
    /off|on/,
  );
});

test("google sign-in stays hidden when oauth is disabled", async ({ page }) => {
  await page.route("**/api/v1/auth/google-config", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ client_id: "", enabled: false }),
    });
  });
  await page.goto("/login");
  await expect(page.locator("[data-google-oauth]")).toHaveAttribute(
    "data-google-oauth",
    "off",
  );
  await expect(
    page.getByRole("button", { name: /使用 Google 继续|Continue with Google/ }),
  ).toHaveCount(0);
});

test("google sign-in opens the public callback redirect", async ({ page }) => {
  await page.route("**/api/v1/auth/google-config", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        client_id: "e2e-client.apps.googleusercontent.com",
        enabled: true,
      }),
    });
  });
  await page.addInitScript(() => {
    Object.defineProperty(window, "open", {
      configurable: true,
      value: (url) => {
        window.__googleOAuthUrl = String(url);
        return null;
      },
    });
  });
  await page.goto("/login");
  await expect(
    page.getByRole("button", { name: /使用 Google 继续|Continue with Google/ }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: /使用 Google 继续|Continue with Google/ })
    .click();
  const oauthUrl = await page.evaluate(() => window.__googleOAuthUrl);
  expect(oauthUrl).toContain("accounts.google.com");
  expect(oauthUrl).toContain("e2e-client.apps.googleusercontent.com");
  expect(decodeURIComponent(oauthUrl ?? "")).toContain("/google-callback");
});

test("unauthenticated console routes redirect to login", async ({ page }) => {
  await stubCaptcha(page);
  await page.goto("/servers");
  await expect(page).toHaveURL(/\/login/);
});

test("admin can sign in through the captcha form", async ({ page }) => {
  await loginViaUi(page);
  await expect(
    page.getByRole("heading", { name: /欢迎回来，admin|Welcome back, admin/ }),
  ).toBeVisible();
});
