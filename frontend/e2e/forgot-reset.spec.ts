import { expect, test } from "@playwright/test";
import { stubCaptcha } from "./helpers/captcha";

test("forgot-password page renders email, captcha, and back link", async ({
  page,
}) => {
  await page.goto("/forgot-password");
  await expect(
    page.getByRole("heading", { name: /忘记密码|Forgot password/i }),
  ).toBeVisible();
  await expect(page.getByLabel(/邮箱地址|Email address/i)).toBeVisible();
  await expect(page.getByAltText(/验证码|Captcha/i)).toBeVisible();
  await expect(
    page.getByRole("button", { name: /发送重置链接|Send reset link/i }),
  ).toBeVisible();
  await expect(page.getByRole("link", { name: /返回登录|Back to sign in/i })).toBeVisible();
});

test("login forgot-password link opens the reset request form", async ({
  page,
}) => {
  await page.goto("/login");
  await page.getByRole("link", { name: /忘记密码|Forgot password/i }).click();
  await expect(page).toHaveURL(/\/forgot-password$/);
  await expect(page.getByLabel(/邮箱地址|Email address/i)).toBeVisible();
});

test("forgot-password rejects a dummy captcha without sending mail", async ({
  page,
}) => {
  await stubCaptcha(page);
  await page.goto("/forgot-password");
  await page.getByLabel(/邮箱地址|Email address/i).fill("nobody@example.com");
  await page.getByRole("textbox", { name: /验证码|^Captcha$/i }).fill("XXXX");
  await page.getByRole("button", { name: /发送重置链接|Send reset link/i }).click();
  await expect(page.getByRole("alert")).toBeVisible();
  await expect(page.getByLabel(/邮箱地址|Email address/i)).toBeVisible();
});

test("reset-password without a token shows an invalid-link state", async ({
  page,
}) => {
  await page.goto("/reset-password");
  await expect(
    page.getByText(/重置链接缺失或无效|missing or invalid/i),
  ).toBeVisible();
  await expect(page.getByLabel(/新密码|New password/i)).toHaveCount(0);
  await expect(
    page.getByRole("link", { name: /重新申请|Request a new reset link/i }),
  ).toBeVisible();
});

test("reset-password with a dummy token stays on the form after a safe reject", async ({
  page,
}) => {
  await page.goto("/reset-password?token=e2e-invalid-token");
  await expect(
    page.getByRole("heading", { name: /重置密码|Reset password/i }),
  ).toBeVisible();
  await page.getByRole("textbox", { name: /^新密码$|^New password$/i }).fill("unused-pass");
  await page
    .getByRole("textbox", { name: /确认新密码|Confirm new password/i })
    .fill("unused-pass");
  await page.getByRole("button", { name: /重置密码|Reset password/i }).click();
  await expect(page.getByRole("alert")).toBeVisible();
  await expect(page.getByRole("textbox", { name: /^新密码$|^New password$/i })).toBeVisible();
});
