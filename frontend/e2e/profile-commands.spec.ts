import { expect, test } from "@playwright/test";
const mockUrl = `http://127.0.0.1:${process.env.PROFILE_MOCK_PORT ?? "38161"}`;
test.beforeEach(async ({ context, request, baseURL }) => {
  await request.get(`${mockUrl}/__test__/reset`);
  await context.addCookies([{ name: "upkk_access_token", value: "fixture-session", url: baseURL! }, { name: "locale", value: "en-US", url: baseURL! }]);
});
for (const width of [390, 1440]) {
  test(`personal registry saves, reloads, revokes and retains failed edits at ${width}px`, async ({ page, request }) => {
    await page.setViewportSize({ width, height: 1000 });
    const errors: string[] = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.goto("/settings/profile#profile-game-commands");
    const form = page.getByTestId("game-command-form");
    await expect(form).toBeVisible();
    await form.getByRole("button", { name: "Add command", exact: true }).click();
    await page.locator("#command-0-name").fill("css_custom");
    await page.locator("#command-0-description").fill("Custom duration.");
    await form.getByRole("button", { name: "Add argument", exact: true }).click();
    await page.locator("#command-0-arg-0-kind").selectOption("integer");
    await page.locator("#command-0-arg-0-description").fill("Seconds from 1 to 7200.");
    await page.locator("#command-0-arg-0-length").fill("4");
    await page.locator("#command-0-arg-0-max").fill("7200");
    await form.getByLabel("Require zero human players").uncheck();
    await form.getByRole("button", { name: "Save game commands", exact: true }).click();
    await expect(form.getByRole("status")).toContainText("Game commands saved");
    const saved = await (await request.get(`${mockUrl}/__test__/commands`)).json();
    expect(saved[0]).toMatchObject({ name: "css_custom", requires_empty: false, requires_online: true, arguments: [{ kind: "integer", minimum: 1, maximum: 7200, max_length: 4 }] });
    await page.reload();
    await expect(page.locator("#command-0-name")).toHaveValue("css_custom");
    await form.getByLabel("Enabled", { exact: true }).uncheck();
    await form.getByRole("button", { name: "Save game commands", exact: true }).click();
    await expect(form.getByRole("status")).toContainText("Game commands saved");
    await page.reload();
    await expect(form.getByLabel("Enabled", { exact: true })).not.toBeChecked();
    await request.get(`${mockUrl}/__test__/fail`);
    await page.locator("#command-0-description").fill("Retain this edit.");
    await form.getByRole("button", { name: "Save game commands", exact: true }).click();
    await expect(form.getByRole("status")).toContainText("Fixture persistence failed");
    await expect(page.locator("#command-0-description")).toHaveValue("Retain this edit.");
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    expect(errors).toEqual([]);
  });
}
