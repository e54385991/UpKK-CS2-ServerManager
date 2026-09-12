import { defineConfig, devices } from "@playwright/test";

const mockPort = process.env.PERF_MOCK_PORT ?? "38131";
const port = process.env.PERF_TEST_PORT ?? "31831";
const production = process.env.PERF_PRODUCTION === "1";
const baseURL = `http://127.0.0.1:${port}`;
export default defineConfig({
  testDir: "./e2e",
  testMatch: /(?:performance|workspace-nav)\.spec\.ts/,
  testIgnore: /overview-performance/,
  outputDir: "test-results/performance-runs",
  workers: 1, retries: 0, timeout: 45_000,
  expect: { timeout: 10_000 },
  use: { ...devices["Desktop Chrome"], baseURL, trace: "retain-on-failure" },
  webServer: [
    { command: "node e2e/performance.mock.mjs", url: `http://127.0.0.1:${mockPort}/health`, env: { PERF_MOCK_PORT: mockPort }, reuseExistingServer: false },
    {
      command: production ? "node scripts/with-internal-api-url.mjs" : `node node_modules/next/dist/bin/next dev --hostname 127.0.0.1 --port ${port}`,
      url: `${baseURL}/deployment-tutorial`,
      env: { INTERNAL_API_URL: `http://127.0.0.1:${mockPort}`, PUBLIC_APP_URL: baseURL, SESSION_COOKIE_SUFFIX: "", PORT: port, HOSTNAME: "127.0.0.1" },
      reuseExistingServer: false, timeout: 120_000,
    },
  ],
});
