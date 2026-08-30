import { mkdir } from "node:fs/promises";
import { dirname } from "node:path";
import type { APIRequestContext, Page } from "@playwright/test";
import { expect } from "@playwright/test";
import { readCaptchaCode } from "./redis";

/** Storage state written by `auth.setup.ts` and reused by signed-in projects. */
export const AUTH_STATE_PATH = "e2e/.auth/admin.json";

export function e2eCredentials(): { username: string; password: string } {
  return {
    username: process.env.E2E_USERNAME?.trim() || "admin",
    password: process.env.E2E_PASSWORD?.trim() || "admin123",
  };
}

type ChallengeJson = { token?: unknown };

async function captchaFromChallenge(request: APIRequestContext): Promise<{
  token: string;
  code: string;
}> {
  const response = await request.get(`/api/captcha/challenge?ts=${Date.now()}`);
  if (!response.ok()) {
    throw new Error(`CAPTCHA challenge failed: HTTP ${response.status()}`);
  }
  const body = (await response.json()) as ChallengeJson;
  if (typeof body.token !== "string" || !body.token) {
    throw new Error("CAPTCHA challenge did not return a token");
  }
  return { token: body.token, code: await readCaptchaCode(body.token) };
}

/**
 * Log in through the Next proxy using the real CAPTCHA flow. The code is read
 * from Redis in this test process — production captcha stays enabled.
 */
export async function loginViaApi(request: APIRequestContext): Promise<void> {
  const { username, password } = e2eCredentials();
  const { token, code } = await captchaFromChallenge(request);
  const response = await request.post("/api/auth/login", {
    data: {
      username,
      password,
      captcha_token: token,
      captcha_code: code,
    },
  });
  if (!response.ok()) {
    const detail = await response.text();
    throw new Error(`Login failed: HTTP ${response.status()} ${detail}`);
  }
}

export async function writeAdminStorageState(
  request: APIRequestContext,
): Promise<void> {
  await mkdir(dirname(AUTH_STATE_PATH), { recursive: true });
  await loginViaApi(request);
  await request.storageState({ path: AUTH_STATE_PATH });
}

/**
 * Fill the login form the way a user would, solving CAPTCHA via Redis.
 */
export async function loginViaUi(page: Page): Promise<void> {
  const { username, password } = e2eCredentials();
  let token = "";
  page.on("response", (response) => {
    if (!response.url().includes("/api/captcha/challenge") || !response.ok()) {
      return;
    }
    void response.json().then((body: ChallengeJson) => {
      if (typeof body.token === "string" && body.token) {
        token = body.token;
      }
    });
  });
  await page.goto("/login");
  await expect.poll(() => token, { timeout: 15_000 }).not.toBe("");
  const code = await readCaptchaCode(token);

  await page.getByLabel(/用户名|Username/i).fill(username);
  await page.getByLabel(/密码|Password/i).fill(password);
  await page.getByRole("textbox", { name: /验证码|Captcha/i }).fill(code);
  await page.getByRole("button", { name: /登录|Sign in/i }).click();
  await expect(page).toHaveURL(/\/overview/, { timeout: 20_000 });
}

export async function sessionBearer(page: Page): Promise<string> {
  const cookies = await page.context().cookies();
  const token = cookies.find((cookie) => cookie.name === "upkk_access_token")
    ?.value;
  if (!token) {
    throw new Error("Session cookie upkk_access_token is missing");
  }
  return token;
}
