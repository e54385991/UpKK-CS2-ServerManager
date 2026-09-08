import { writeFile } from "node:fs/promises";
import { test, expect } from "@playwright/test";

for (const locale of ["zh-CN", "en-US"] as const) {
  for (const width of [390, 1440]) {
    test(`${locale} ${width}: configure, submit, cancel and verify saved token`, async ({ page, context }) => {
      await context.addCookies([
        { name: "upkk_access_token", value: "isolated-test-session", domain: "localhost", path: "/" },
        { name: "locale", value: locale, domain: "localhost", path: "/" },
      ]);
      // Ordinary HTTP LAN origins do not expose crypto.randomUUID.
      if (width === 390) await page.addInitScript(() => {
        Object.defineProperty(crypto, "randomUUID", { value: undefined, configurable: true });
      });
      await page.setViewportSize({ width, height: 900 });
      const errors: string[] = [];
      page.on("pageerror", error => errors.push(error.message));
      await page.goto("/plugins");
      await page.getByRole("button", { name: locale === "zh-CN" ? "AI 智能导入" : "AI discovery", exact: true }).click();
      const dialog = page.getByRole("dialog", { name: locale === "zh-CN" ? "AI 智能导入" : "AI discovery" });
      await expect(dialog).toContainText("test-model");
      await expect(dialog.locator("#ai-framework")).toHaveValue("all");
      await dialog.locator("#ai-framework").selectOption("other");
      await expect(dialog.locator("#ai-framework")).toHaveValue("other");
      await dialog.locator("#ai-framework").selectOption("all");
      await expect(dialog.locator("#ai-min_stars")).toHaveValue("10");
      await expect(dialog.locator("#ai-updated_within_days")).toHaveValue("90");
      // Default ordering is stars, then most recently updated, then forks.
      await expect(dialog.locator("#ai-sort-0")).toHaveValue("stars");
      await expect(dialog.locator("#ai-sort-1")).toHaveValue("updated");
      await expect(dialog.locator("#ai-sort-2")).toHaveValue("forks");
      // Promoting "updated" to first pushes stars down rather than duplicating.
      await dialog.locator("#ai-sort-0").selectOption("updated");
      await expect(dialog.locator("#ai-sort-1")).toHaveValue("stars");
      await dialog.locator("#ai-sort-0").selectOption("stars");
      const submit = dialog.locator('button[type="submit"]');
      await expect(submit).toBeDisabled();
      await dialog.locator("#ai-acknowledge").check();
      await expect(submit).toBeEnabled();
      await page.screenshot({ path: `/tmp/plugin-ai-modal-${locale}-${width}.png`, fullPage: true });
      expect(await dialog.evaluate(el => el.scrollWidth <= el.clientWidth + 1)).toBeTruthy();
      if (locale === "en-US" && width === 1440) {
        await page.route("**/plugins", route => route.request().headers()["next-action"] ? route.abort("failed") : route.continue());
        await submit.click();
        await expect(dialog.getByRole("alert")).toHaveText("Request failed. Check the configuration or try again later.");
        await expect(submit).toBeEnabled();
        await page.unroute("**/plugins");
      }
      await submit.click();
      await expect(dialog).toBeHidden();
      await expect(page.getByText("Searching maintained CS2 plugins").first()).toBeVisible();
      const usage = page.getByTestId("ai-import-usage");
      await expect(usage).toBeVisible();
      await expect(usage).toContainText(locale === "zh-CN" ? "AI 思考中" : "AI is thinking");
      await expect(page.getByTestId("ai-tokens-input")).toHaveText("1,200");
      const firstCount = await page.getByTestId("ai-tokens-output").textContent();
      await expect(page.getByTestId("ai-tokens-output")).not.toHaveText(firstCount ?? "");
      expect(await usage.evaluate(el => {
        const box = el.getBoundingClientRect();
        return el.scrollWidth <= el.clientWidth + 1 && box.left >= 0 && box.right <= innerWidth;
      })).toBeTruthy();
      await page.screenshot({ path: `/tmp/plugin-ai-usage-${locale}-${width}.png`, fullPage: true });
      await page.getByRole("button", { name: locale === "zh-CN" ? "取消任务" : "Cancel job", exact: true }).click();
      await expect(page.getByRole("button", { name: locale === "zh-CN" ? "取消任务" : "Cancel job", exact: true })).toBeHidden();
      const deleteTask = page.getByRole("button", { name: locale === "zh-CN" ? "删除任务记录" : "Delete task history", exact: true });
      await expect(deleteTask).toBeVisible();
      await deleteTask.click();
      await expect(deleteTask).toBeHidden();
      await page.reload();
      await expect(page.getByText("Searching maintained CS2 plugins")).toHaveCount(0);
      await page.goto("/settings");
      await expect(page.getByText("test-admin").last()).toBeVisible();
      await page.getByRole("button", { name: locale === "zh-CN" ? "验证已保存的全局 GitHub Token" : "Verify saved global GitHub token", exact: true }).click();
      await expect(page.getByText("Core: 4900 · Search: 29")).toBeVisible();
      await page.goto("/plugins/1");
      await page.locator("#ai-rule-asset").fill("plugin-*.zip");
      await page.getByRole("button", { name: locale === "zh-CN" ? "保存配置并标记已核对" : "Save and mark reviewed", exact: true }).click();
      await expect(page.locator("#ai-rule-asset")).toHaveValue("plugin-*.zip");
      await expect(page.getByText("publish/Plugin → addons/counterstrikesharp/plugins/Plugin", { exact: true })).toBeVisible();
      if (locale === "zh-CN" && width === 390) {
        for (const name of ["get_compilation_issues", "get_errors"]) {
          const response = await page.request.post("/_next/mcp", { headers: { accept: "application/json, text/event-stream" }, data: { jsonrpc: "2.0", id: 1, method: "tools/call", params: { name, arguments: {} } } });
          const result = await response.text();
          await writeFile(`/tmp/plugin-ai-next-${name}.txt`, result);
          expect(result).not.toContain('"isError":true');
        }
      }
      expect(errors).toEqual([]);
    });
  }
}
