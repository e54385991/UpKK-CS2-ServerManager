import { test as setup } from "@playwright/test";
import { writeAdminStorageState } from "./helpers/auth";

setup("store admin session from CAPTCHA + Redis", async ({ request }) => {
  await writeAdminStorageState(request);
});
