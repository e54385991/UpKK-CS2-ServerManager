import { expect, test } from "@playwright/test";

test("google callback stays public and reports a missing token", async ({
  page,
}) => {
  await page.goto("/google-callback");
  await expect(page).toHaveURL(/\/google-callback$/);
  await expect(page.getByRole("status")).toContainText(
    /登录失败|Authentication failed/,
  );
});

test("google callback posts the id token to the opener", async ({ page }) => {
  await page.goto("/login");
  const token = await page.evaluate(() => {
    return new Promise<string>((resolve) => {
      window.addEventListener("message", (event) => {
        const data = event.data as { type?: string; id_token?: string };
        if (data?.type === "google-oauth-token" && data.id_token) {
          resolve(data.id_token);
        }
      });
      window.open("/google-callback#id_token=e2e-google-id-token", "e2e-google");
    });
  });
  expect(token).toBe("e2e-google-id-token");
});
