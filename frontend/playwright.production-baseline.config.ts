import { defineConfig, devices } from "@playwright/test";

const mockPort = process.env.PERF_MOCK_PORT ?? "38151";
const port = process.env.PERF_TEST_PORT ?? "31851";
const baseURL = `http://127.0.0.1:${port}`;

export default defineConfig({
  testDir: "./e2e",
  testMatch: /production-baseline\.spec\.ts/,
  outputDir: "test-results/perf-baseline-runs",
  workers: 1,
  retries: 0,
  timeout: 120_000,
  expect: { timeout: 15_000 },
  use: { ...devices["Desktop Chrome"], baseURL, trace: "retain-on-failure" },
  webServer: [
    {
      command: "node e2e/performance.mock.mjs",
      url: `http://127.0.0.1:${mockPort}/health`,
      env: { PERF_MOCK_PORT: mockPort },
      reuseExistingServer: false,
    },
    {
      command: "node scripts/with-internal-api-url.mjs",
      url: `${baseURL}/login`,
      env: {
        INTERNAL_API_URL: `http://127.0.0.1:${mockPort}`,
        PUBLIC_APP_URL: baseURL,
        SESSION_COOKIE_SUFFIX: "",
        PORT: port,
        HOSTNAME: "127.0.0.1",
        PERF_PRODUCTION: "1",
      },
      reuseExistingServer: false,
      timeout: 120_000,
    },
  ],
});
