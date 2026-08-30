import { expect, test } from "@playwright/test";
import { stubCaptcha } from "./helpers/captcha";

test("unauthenticated settings profile redirects to login with next", async ({
  page,
}) => {
  await stubCaptcha(page);
  await page.goto("/settings/profile");
  await expect(page).toHaveURL(/\/login\?next=%2Fsettings%2Fprofile/);
  await expect(page.getByLabel(/用户名|Username/i)).toBeVisible();
  await expect(page.getByAltText(/验证码|Captcha/i)).toBeVisible();
});

test("unauthenticated system settings redirects to login", async ({ page }) => {
  await stubCaptcha(page);
  await page.goto("/settings");
  await expect(page).toHaveURL(/\/login\?next=%2Fsettings/);
});
