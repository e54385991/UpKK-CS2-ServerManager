import { expect, test } from "@playwright/test";

test("deployment tutorial is public and shows the illustrated steps", async ({
  page,
}) => {
  await page.goto("/deployment-tutorial");
  await expect(page).toHaveURL(/\/deployment-tutorial$/);
  await expect(page.getByTestId("tutorial-guide")).toBeVisible();
  await expect(
    page.getByRole("heading", {
      name: /如何使用本面板部署|How to deploy a CS2/,
    }),
  ).toBeVisible();
  await expect(
    page.locator('img[src*="/static/images/aliyun-deploy/1.webp"]'),
  ).toBeVisible();
  await expect(page.getByRole("link", { name: /返回登录|Back to sign in/ })).toBeVisible();

  const lastStep = page.getByTestId("tutorial-step-10");
  await lastStep.scrollIntoViewIfNeeded();
  await expect(lastStep).toBeInViewport();
  await expect(
    page.getByRole("heading", { name: /步骤 10|Step 10/ }),
  ).toBeVisible();
});
