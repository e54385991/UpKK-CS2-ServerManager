import { defineConfig, devices } from "@playwright/test";

const mockPort = process.env.MONITOR_MOCK_PORT ?? "38141";
const nextPort = process.env.MONITOR_TEST_PORT ?? "31841";
const mockUrl = `http://127.0.0.1:${mockPort}`;
const baseURL = `http://127.0.0.1:${nextPort}`;

export default defineConfig({
  testDir: "./e2e",
  testMatch: /monitor-dashboard\.spec\.ts/,
  workers: 1,
  retries: 0,
  timeout: 45_000,
  expect: { timeout: 8_000 },
  use: { ...devices["Desktop Chrome"], baseURL, trace: "retain-on-failure" },
  webServer: [
    {
      command: "node e2e/monitor.mock.mjs",
      url: `${mockUrl}/health`,
      env: { MONITOR_MOCK_PORT: mockPort },
      reuseExistingServer: false,
    },
    {
      command: `node node_modules/next/dist/bin/next dev --hostname 127.0.0.1 --port ${nextPort}`,
      url: `${baseURL}/deployment-tutorial`,
      env: { INTERNAL_API_URL: mockUrl, PUBLIC_APP_URL: baseURL, SESSION_COOKIE_SUFFIX: "" },
      reuseExistingServer: false,
      timeout: 120_000,
    },
  ],
});
