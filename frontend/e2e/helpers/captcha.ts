import type { Page } from "@playwright/test";

/** 1×1 PNG so the login <img alt="Captcha"> still paints. */
const STUB_IMAGE =
  "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==";

/**
 * Intercept CAPTCHA fetches for route-guard tests. Those pages hydrate login
 * after redirect and would otherwise exhaust FastAPI's 60/min captcha limit
 * before the real sign-in tests run. Production captcha is unchanged.
 */
export async function stubCaptcha(page: Page): Promise<void> {
  await page.route("**/api/captcha/**", async (route) => {
    const url = route.request().url();
    if (url.includes("/challenge")) {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ token: "e2e-guard-stub", image: STUB_IMAGE }),
      });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: "image/png",
      body: Buffer.from(STUB_IMAGE.split(",")[1] ?? "", "base64"),
      headers: { "X-Captcha-Token": "e2e-guard-stub" },
    });
  });
}
