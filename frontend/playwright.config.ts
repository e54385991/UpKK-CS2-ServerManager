import { defineConfig, devices } from "@playwright/test";
import { AUTH_STATE_PATH } from "./e2e/helpers/auth";

const chrome = { ...devices["Desktop Chrome"], channel: "chrome" as const };

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 2 : 0,
  timeout: 45_000,
  expect: { timeout: 15_000 },
  use: {
    baseURL: process.env.PLAYWRIGHT_BASE_URL ?? "http://localhost:3000",
    trace: "on-first-retry",
  },
  webServer: {
    command: "npm run dev",
    url: "http://localhost:3000/login",
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
  projects: [
    {
      name: "setup",
      testMatch: /auth\.setup\.ts/,
      use: chrome,
    },
    {
      name: "anon",
      testMatch:
        /login\.spec\.ts|console-routes\.spec\.ts|settings-profile\.spec\.ts|forgot-reset\.spec\.ts|register\.spec\.ts|tutorial\.spec\.ts|google-oauth\.spec\.ts/,
      use: chrome,
    },
    {
      name: "signed-in",
      dependencies: ["setup"],
      testMatch: /authenticated-.*\.spec\.ts/,
      use: {
        ...chrome,
        storageState: AUTH_STATE_PATH,
      },
    },
  ],
});
