import { expect, test } from "@playwright/test";

test("add server only allows picking an initialized account", async ({
  page,
}) => {
  await page.goto("/servers/new");
  await expect(page.getByTestId("create-init-gate")).toBeVisible();
  await expect(
    page
      .getByTestId("initialized-host-select")
      .or(page.getByTestId("initialized-hosts-empty")),
  ).toBeVisible({ timeout: 15_000 });

  await expect(page.locator("input#host")).toHaveCount(0);
  await expect(page.locator("input#sshUser")).toHaveCount(0);
  await expect(page.locator("input#sshPassword")).toHaveCount(0);
  await expect(page.locator("input#sshPort")).toHaveCount(0);
  await expect(page.locator("input#gameDirectory")).toHaveCount(0);

  await page.getByRole("link", { name: /去初始化主机|Go to host setup/ }).click();
  await expect(page).toHaveURL(/tab=setup/);
  await expect(page.getByTestId("setup-wizard")).toBeVisible();
});

test("server detail offers clone flow with the source server parameter", async ({
  page,
}) => {
  await page.goto("/servers/1");
  await page
    .getByRole("link", { name: /基于现有服务器添加|Add based on existing server/ })
    .click();
  await expect(page).toHaveURL(/\/servers\/new\?sourceServerId=1$/);

  await expect(
    page
      .getByRole("heading", { name: /来源服务器|Source server/ })
      .or(page.getByText(/无法加载来源服务器|Unable to load the source server/)),
  ).toBeVisible();
});
