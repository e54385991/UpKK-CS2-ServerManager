import { expect, test } from "@playwright/test";
import { stubCaptcha } from "./helpers/captcha";

test("register page renders fields, captcha, and sign-in link", async ({
  page,
}) => {
  await page.goto("/register");
  await expect(
    page.getByRole("heading", { name: /注册|Create account/i }),
  ).toBeVisible();
  await expect(page.getByLabel(/用户名|Username/i)).toBeVisible();
  await expect(page.getByLabel(/邮箱地址|Email address/i)).toBeVisible();
  await expect(page.getByRole("textbox", { name: /^密码$|^Password$/i })).toBeVisible();
  await expect(
    page.getByRole("textbox", { name: /确认密码|Confirm password/i }),
  ).toBeVisible();
  await expect(page.getByAltText(/验证码|Captcha/i)).toBeVisible();
  await expect(page.getByRole("button", { name: /注册|Create account/i })).toBeVisible();
  await expect(page.getByRole("link", { name: /去登录|Sign in/i })).toBeVisible();
});

test("login register link opens the create-account form", async ({ page }) => {
  await page.goto("/login");
  await page.getByRole("link", { name: /去注册|Create one/i }).click();
  await expect(page).toHaveURL(/\/register$/);
  await expect(page.getByLabel(/用户名|Username/i)).toBeVisible();
});

test("register rejects a dummy captcha without creating a user", async ({
  page,
}) => {
  await stubCaptcha(page);
  await page.goto("/register");
  await page.getByLabel(/用户名|Username/i).fill("e2e-register-reject");
  await page.getByLabel(/邮箱地址|Email address/i).fill("e2e-register-reject@example.com");
  await page.getByRole("textbox", { name: /^密码$|^Password$/i }).fill("unused-pass");
  await page
    .getByRole("textbox", { name: /确认密码|Confirm password/i })
    .fill("unused-pass");
  await page.getByRole("textbox", { name: /验证码|^Captcha$/i }).fill("XXXX");
  await page.getByRole("button", { name: /注册|Create account/i }).click();
  await expect(page.getByRole("alert")).toBeVisible();
  await expect(page.getByLabel(/用户名|Username/i)).toBeVisible();
  await expect(page).toHaveURL(/\/register$/);
});
