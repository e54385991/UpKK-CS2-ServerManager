import { expect, type Page } from "@playwright/test";
import en from "../../src/i18n/messages/en-US.json" with { type: "json" };
import zh from "../../src/i18n/messages/zh-CN.json" with { type: "json" };

export async function expectPageContent(page: Page, path: string, locale: string) {
  const t = locale === "zh-CN" ? zh : en;
  const pathname = path.split("?")[0]!;
  const visibleTestId = (id: string) => page.getByTestId(id).filter({ visible: true });
  const visibleText = (text: string) => page.getByText(text, { exact: true }).filter({ visible: true }).first();
  const ids: Record<string, string> = {
    "/": "overview-stats", "/overview": "overview-stats", "/servers/new": "create-init-gate",
    "/servers/initialized": "initialized-hosts-manager", "/servers/1/config": "game-config-form",
    "/servers/1/host-config": "host-config-form", "/servers/1/monitoring": "monitoring-form",
    "/servers/1/cleanup": "cleanup-console", "/servers/1/game-modes": "game-modes-wizard",
    "/servers/1/backups": "s3-backup-now", "/settings/profile": "profile-category-nav",
    "/settings": "settings-category-nav",
  };
  if (ids[pathname]) { await expect(visibleTestId(ids[pathname]!)).toBeVisible(); return; }
  if (pathname === "/login") { await expect(page.getByRole("button", { name: t.login.submit, exact: true })).toBeVisible(); return; }
  if (pathname === "/register") { await expect(visibleText(t.register.disabledTitle)).toBeVisible(); return; }
  if (pathname === "/forgot-password") { await expect(page.getByRole("button", { name: t.forgotPassword.submit, exact: true })).toBeVisible(); return; }
  if (pathname === "/reset-password") { await expect(page.getByRole("button", { name: t.resetPassword.submit, exact: true })).toBeVisible(); return; }
  if (pathname === "/google-callback") { await expect(visibleTestId("google-callback-shell")).toBeVisible(); return; }
  if (pathname === "/deployment-tutorial") { await expect(visibleTestId("tutorial-step-10")).toBeVisible(); return; }
  if (pathname === "/servers") { await expect(page.locator('a[href="/servers/1"]').filter({ visible: true }).first()).toBeVisible(); return; }
  if (pathname === "/plugins") { await expect(page.getByRole("link", { name: "Fixture Plugin", exact: true }).filter({ visible: true })).toBeVisible(); return; }
  if (pathname === "/plugins/1") { await expect(visibleText("Fixture Plugin")).toBeVisible(); return; }
  if (pathname === "/assistant") { await expect(page.locator("textarea").filter({ visible: true })).toBeVisible(); return; }
  if (pathname === "/audit") { await expect(visibleText(t.audit.empty)).toBeVisible(); return; }
  if (pathname === "/servers/1/files") { await expect(visibleText("server.cfg")).toBeVisible(); return; }
  const texts: Record<string, string> = {
    "/servers/1/additional-fixes": t.serverAdditionalFixes.commandTitle,
    "/servers/1/frameworks": t.serverDetail.frameworks.baseTitle,
    "/servers/1/help": t.serverHelp.execstackTitle,
    "/servers/1/plugin-configs": t.pluginConfigs.noSources,
    "/servers/1/plugins": t.plugins.installedEmpty,
    "/servers/1/schedule": t.schedule.empty,
    "/servers/1/discord": t.discord.bindingTitle,
    "/settings/discord": t.discord.globalTitle,
    "/servers/1/updates": t.gameUpdates.title,
    "/servers/1/console": t.console.launcherTitle,
    "/live-console/1": t.console.launcherTitle,
  };
  if (texts[pathname]) { await expect(visibleText(texts[pathname]!)).toBeVisible(); return; }
  // Remaining workspace projections still must resolve authorized identity.
  await expect(page.locator("h1").filter({ visible: true })).toContainText("fixture-a-server-1");
}
