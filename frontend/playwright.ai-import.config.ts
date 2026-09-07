import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  testMatch: ["ai-import.spec.ts", "github-credentials.spec.ts"],
  workers: 1,
  timeout: 45_000,
  use: { ...devices["Desktop Chrome"], channel: "chrome", baseURL: "http://localhost:31801" },
  webServer: [
    { command: "node e2e/ai-import.mock.mjs", url: "http://127.0.0.1:38111/api/v1/auth/me", reuseExistingServer: false },
    { command: "INTERNAL_API_URL=http://127.0.0.1:38111 npx next dev --port 31801", url: "http://localhost:31801/login", reuseExistingServer: false, timeout: 120_000 },
  ],
});
