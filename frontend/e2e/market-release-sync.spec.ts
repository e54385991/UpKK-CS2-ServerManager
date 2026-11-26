import { expect, test } from "@playwright/test";

test("reviews a changed package and queues selected servers from the marketplace", async ({ page, context }) => {
  await context.addCookies([
    { name: "upkk_access_token", value: "isolated-test-session", domain: "localhost", path: "/" },
    { name: "locale", value: "en-US", domain: "localhost", path: "/" },
  ]);
  await page.addInitScript(() => {
    Object.defineProperty(crypto, "randomUUID", { value: undefined, configurable: true });
  });
  await page.setViewportSize({ width: 390, height: 900 });
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));

  await page.goto("/plugins");
  await page.getByTestId("market-release-sync-open").click();
  const dialog = page.getByRole("dialog", { name: "Sync release for AI Test Plugin" });
  await expect(dialog.getByText("Choose one ordinary Linux package")).toBeVisible();
  const save = dialog.getByRole("button", { name: "Save release" });
  await expect(save).toBeDisabled();
  const packages = dialog.getByLabel("Upstream packages");
  await expect(packages.locator('option[value$="relwithdebinfo.tar.gz"]')).toBeDisabled();
  await packages.selectOption("ClientCvarValue-v1.1.0-linux.tar.gz");
  await expect(dialog.getByLabel("Asset filename pattern")).toHaveValue("ClientCvarValue-*-linux.tar.gz");
  await save.click();

  await expect(dialog.getByText("Synchronized release v1.1.0")).toBeVisible();
  const classic = dialog.getByRole("checkbox", { name: /Classic/ });
  const competitive = dialog.getByRole("checkbox", { name: /Competitive/ });
  await expect(classic).not.toBeChecked();
  await expect(competitive).not.toBeChecked();
  await classic.check();
  await competitive.check();
  await dialog.getByRole("button", { name: "Queue updates (2)" }).click();
  await expect(dialog.getByText("Competitive · Server queue is full")).toBeVisible();
  await expect(dialog.getByText("1 server update queued")).toBeVisible();
  await dialog.getByRole("button", { name: "Close release sync" }).click();
  await expect(page.getByText("plugin-auto-update market ClientCvarValue v1.1.0").first()).toBeVisible();

  for (const name of ["get_compilation_issues", "get_errors"]) {
    const response = await page.request.post("/_next/mcp", {
      headers: { accept: "application/json, text/event-stream" },
      data: { jsonrpc: "2.0", id: 1, method: "tools/call", params: { name, arguments: {} } },
    });
    expect(response.ok()).toBeTruthy();
    expect(await response.text()).not.toContain('"isError":true');
  }
  expect(errors).toEqual([]);
});
