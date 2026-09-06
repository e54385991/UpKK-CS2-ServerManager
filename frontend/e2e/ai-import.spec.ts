import { writeFile } from "node:fs/promises";
import { test, expect } from "@playwright/test";

for (const locale of ["zh-CN", "en-US"] as const) {
  for (const width of [390, 1440]) {
    test(`${locale} ${width}: configure, submit, cancel and verify saved token`, async ({ page, context }) => {
      await context.addCookies([
        { name: "upkk_access_token", value: "isolated-test-session", domain: "localhost", path: "/" },
        { name: "locale", value: locale, domain: "localhost", path: "/" },
      ]);
      await page.setViewportSize({ width, height: 900 });
      const errors: string[] = [];
      page.on("pageerror", error => errors.push(error.message));
      await page.goto("/plugins");
      await page.getByRole("button", { name: locale === "zh-CN" ? "AI 智能导入" : "AI discovery", exact: true }).click();
      const dialog = page.getByRole("dialog", { name: locale === "zh-CN" ? "AI 智能导入" : "AI discovery" });
      await expect(dialog).toContainText("test-model");
      await expect(dialog.locator("#ai-framework")).toHaveValue("all");
      await expect(dialog.locator("#ai-min_stars")).toHaveValue("10");
      const submit = dialog.locator('button[type="submit"]');
      await expect(submit).toBeDisabled();
      await dialog.getByRole("checkbox").check();
      await expect(submit).toBeEnabled();
      await page.screenshot({ path: `/tmp/plugin-ai-modal-${locale}-${width}.png`, fullPage: true });
      expect(await dialog.evaluate(el => el.scrollWidth <= el.clientWidth + 1)).toBeTruthy();
      await submit.click();
      await expect(dialog).toBeHidden();
      await expect(page.getByText("Searching maintained CS2 plugins").first()).toBeVisible();
      await page.getByRole("button", { name: locale === "zh-CN" ? "取消任务" : "Cancel job", exact: true }).click();
      await expect(page.getByRole("button", { name: locale === "zh-CN" ? "取消任务" : "Cancel job", exact: true })).toBeHidden();
      await page.goto("/settings");
      await expect(page.getByText("test-admin").last()).toBeVisible();
      await page.getByRole("button", { name: locale === "zh-CN" ? "验证已保存的全局 GitHub Token" : "Verify saved global GitHub token", exact: true }).click();
      await expect(page.getByText("Core: 4900 · Search: 29")).toBeVisible();
      await page.goto("/plugins/1");
      await page.locator("#ai-rule-asset").fill("plugin-*.zip");
      await page.getByRole("button", { name: locale === "zh-CN" ? "保存配置并标记已核对" : "Save and mark reviewed", exact: true }).click();
      await expect(page.locator("#ai-rule-asset")).toHaveValue("plugin-*.zip");
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
