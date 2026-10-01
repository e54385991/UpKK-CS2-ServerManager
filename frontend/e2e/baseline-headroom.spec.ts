import { expect, test } from "@playwright/test";
import en from "../src/i18n/messages/en-US.json" with { type: "json" };
import zh from "../src/i18n/messages/zh-CN.json" with { type: "json" };

const mock = `http://127.0.0.1:${process.env.PERF_MOCK_PORT ?? "38131"}`;

for (const locale of ["en-US", "zh-CN"]) {
  for (const width of [390, 1440]) {
    test(`${locale} ${width}: split server forms and maintenance views load and keep drafts`, async ({ page, context, request }) => {
      await request.post(`${mock}/__test__/reset`);
      await context.addCookies([
        { name: "upkk_access_token", value: "fixture-admin", domain: "127.0.0.1", path: "/" },
        { name: "locale", value: locale, domain: "127.0.0.1", path: "/" },
      ]);
      await page.setViewportSize({ width, height: 844 });
      const errors: string[] = [];
      page.on("pageerror", (error) => errors.push(error.message));
      await page.goto("/servers/new?from=fixture");
      await expect(page.locator("#name")).toBeVisible();
      await page.locator("#name").fill("draft-server");
      await page.locator("#gamePort").fill("27016");
      await expect(page.locator("#name")).toHaveValue("draft-server");
      await page.goto("/servers/new?tab=setup");
      await expect(page.getByTestId("setup-wizard")).toBeVisible();
      await page.locator("#setup-host").fill("draft.invalid");
      await page.locator("#setup-cs2-user").fill("draftuser");
      await page.getByTestId("setup-mode-manual").click();
      await expect(page.getByTestId("setup-manual-script")).toContainText("# fixture manual setup");
      await page.getByTestId("setup-mode-auto").click();
      await expect(page.locator("#setup-cs2-user")).toHaveValue("draftuser");
      await expect(page.locator("#setup-host")).toHaveValue("");
      await page.goto("/servers/1/cleanup");
      await expect(page.getByTestId("cleanup-console")).toBeVisible();
      await page.locator("#cleanup-retain-days").fill("14");
      await expect(page.locator("#cleanup-retain-days")).toHaveValue("14");
      await page.goto("/servers/1/updates");
      await expect(page.locator("#auto-update")).toBeVisible();
      await page.locator("#interval").fill("12");
      await expect(page.locator("#interval")).toHaveValue("12");
      expect(errors).toEqual([]);
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    });

    test(`${locale} ${width}: first file upload loads its dock and cancellation releases the request`, async ({ page, context, request }) => {
      const messages = locale === "en-US" ? en : zh;
      await request.post(`${mock}/__test__/reset`);
      await context.addCookies([
        { name: "upkk_access_token", value: "fixture-admin", domain: "127.0.0.1", path: "/" },
        { name: "locale", value: locale, domain: "127.0.0.1", path: "/" },
      ]);
      await page.setViewportSize({ width, height: 844 });
      let release = () => {};
      const waiting = new Promise<void>((resolve) => { release = resolve; });
      await page.route("**/files-upload/servers/1?*", async (route) => {
        await waiting;
        await route.fulfill({ json: { success: true } }).catch(() => {});
      });
      await page.goto("/servers/1/files");
      await expect(page.getByTestId("files-dropzone")).not.toHaveAttribute("aria-busy", "true");
      await expect(page.getByTestId("files-upload-dock")).toHaveCount(0);
      await page.locator('input[type="file"]').first().setInputFiles({
        name: "draft.cfg", mimeType: "text/plain", buffer: Buffer.from("hostname fixture"),
      });
      const dock = page.getByTestId("files-upload-dock");
      await expect(dock).toBeVisible();
      const cancel = dock.getByRole("button", { name: messages.files.uploadCancel, exact: true });
      const aborted = page.waitForEvent("requestfailed", (req) => req.url().includes("/files-upload/servers/1"));
      try {
        await cancel.click();
        await aborted;
        await expect(page.getByRole("button", { name: messages.files.uploadFiles, exact: true })).toBeEnabled();
      } finally {
        release();
      }
    });
  }
}
