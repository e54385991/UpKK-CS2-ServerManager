import { expect, test } from "@playwright/test";

test("deployment tutorial is public and shows the illustrated steps", async ({
  page,
}) => {
  await page.goto("/deployment-tutorial");
  await expect(page).toHaveURL(/\/deployment-tutorial$/);
  await expect(
    page.getByRole("heading", {
      name: /如何使用本面板部署|How to deploy a CS2/,
    }),
  ).toBeVisible();
  await expect(
    page.locator('img[src="/static/images/aliyun-deploy/1.webp"]'),
  ).toBeVisible();
  await expect(page.getByRole("link", { name: /添加服务器|Add a server/ })).toBeVisible();
});
