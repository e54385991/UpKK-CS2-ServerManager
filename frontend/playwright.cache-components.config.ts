import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  testMatch: /cache-components.*\.spec\.ts/,
  outputDir: "test-results/cache-components-current",
  workers: 1,
  retries: 0,
  timeout: 45_000,
  expect: { timeout: 5000 },
  use: { baseURL: "http://127.0.0.1:31961", trace: "retain-on-failure" },
  projects: ["en-US", "zh-CN"].flatMap(locale => [390, 1440].map(width => ({
    name: `${locale}-${width}`, use: { locale, viewport: { width, height: 844 } },
  }))),
  webServer: [
    { command: "node e2e/cache-components.mock.mjs", url: "http://127.0.0.1:38261/health", reuseExistingServer: false },
    { command: "node scripts/run-cache-components.mjs", url: "http://127.0.0.1:31961/login", reuseExistingServer: false, timeout: 60_000 },
  ],
});
