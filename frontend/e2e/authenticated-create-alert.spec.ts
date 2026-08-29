import { expect, test } from "@playwright/test";
import { readCaptchaCode } from "./helpers/redis";

test("adding an uninitialized host opens an alert and jumps to setup", async ({
  page,
}) => {
  let token = "";
  page.on("response", (response) => {
    if (!response.url().includes("/api/captcha/challenge") || !response.ok()) {
      return;
    }
    void response.json().then((body: { token?: unknown }) => {
      if (typeof body.token === "string" && body.token) {
        token = body.token;
      }
    });
  });

  await page.goto("/servers/new");
  await expect(page.locator("#name")).toBeVisible();
  await expect(page.getByTestId("create-init-gate")).toBeVisible();
  await expect.poll(() => token, { timeout: 15_000 }).not.toBe("");
  const code = await readCaptchaCode(token);

  await page.locator("#name").fill("ssh-alert-verify");
  await page.locator("#host").fill("127.0.0.1");
  await page.locator("#sshPort").fill("1");
  await page.locator("#sshUser").fill("root");
  await page.locator("#sshPassword").fill("wrong-password");
  await page.locator("#captcha").fill(code);
  await page.getByRole("button", { name: /创建服务器|Create server/ }).click();

  const alert = page.getByTestId("app-alert");
  await expect(alert).toBeVisible({ timeout: 10_000 });
  await expect(
    alert.getByRole("heading", {
      name: /必须先初始化环境|Initialize the host first/,
    }),
  ).toBeVisible();
  await alert.getByRole("button", { name: /知道了|OK/ }).click();
  await expect(page).toHaveURL(/tab=setup/);
  await expect(page.getByTestId("setup-wizard")).toBeVisible();
  await expect(page.getByTestId("setup-must-initialize")).toBeVisible();
  await expect(page.locator("#setup-host")).toHaveValue("127.0.0.1");
});
