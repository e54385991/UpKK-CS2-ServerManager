import { expect, test } from "@playwright/test";
import { stubCaptcha } from "./helpers/captcha";

const CONSOLE_ROUTES = [
  "/overview",
  "/servers",
  "/servers/new",
  "/servers/1",
  "/servers/1/operations",
  "/servers/1/frameworks",
  "/servers/1/backups",
  "/servers/1/plugins",
  "/servers/1/plugin-configs",
  "/servers/1/updates",
  "/servers/1/maps",
  "/servers/1/files",
  "/servers/1/console",
  "/servers/1/config",
  "/servers/1/monitoring",
  "/servers/1/schedule",
  "/servers/1/discord",
  "/servers/1/help",
  "/plugins",
  "/assistant",
  "/settings",
  "/settings/profile",
  "/settings/discord",
  "/audit",
];

for (const path of CONSOLE_ROUTES) {
  test(`unauthenticated ${path} redirects to login`, async ({ page }) => {
    await stubCaptcha(page);
    await page.goto(path);
    await expect(page).toHaveURL(/\/login/);
  });
}
