import { expect, test } from "@playwright/test";

/**
 * Plugin center render smoke. Read-only: opens the marketplace and one plugin
 * detail page, never installs anything.
 *
 * The detail page is the one console route that renders the install form and a
 * full Markdown description at once, so a render-time crash there (Next's
 * built-in "This page couldn't load" boundary) is invisible to lint, typecheck
 * and the production build — only an actual request catches it.
 */

const ERROR_BOUNDARY = /This page couldn.t load|A server error occurred/;

test("marketplace lists the runtime sections without a render error", async ({
  page,
}) => {
  await page.goto("/plugins");
  await expect(page.getByTestId("market-framework-tabs")).toBeVisible();
  await expect(page.getByText(ERROR_BOUNDARY)).toHaveCount(0);

  await page.getByRole("link", { name: "SwiftlyS2" }).click();
  await expect(page).toHaveURL(/framework=swiftly/);
  await expect(page.getByText(ERROR_BOUNDARY)).toHaveCount(0);
});

test("a plugin detail page renders its description and install panel", async ({
  page,
}) => {
  await page.goto("/plugins");
  const firstPlugin = page
    .locator("li")
    .getByRole("link", { name: /.+/ })
    .filter({ hasNotText: /GitHub/ })
    .first();
  test.skip(
    (await firstPlugin.count()) === 0,
    "the marketplace has no listing to open",
  );

  await firstPlugin.click();
  await expect(page).toHaveURL(/\/plugins\/\d+/);
  await expect(page.getByText(ERROR_BOUNDARY)).toHaveCount(0);
  // The install panel is what regressed: it renders the runtime-mismatch
  // helpers even before a plan exists.
  await expect(page.getByText(/安装$|^Install$/).first()).toBeVisible();
  await expect(page.getByTestId("plugin-github-link")).toBeVisible();
});
