import { fileURLToPath } from "node:url";
import { measureBundles, bundleViolations } from "./bundle-budget.mjs";

const result = await measureBundles(fileURLToPath(new URL("../.next", import.meta.url)));
const violations = bundleViolations(result);
if (violations.length) {
  console.error("Next.js bundle budget exceeded:");
  for (const violation of violations) console.error(`  - ${violation}`);
  process.exitCode = 1;
} else {
  console.log(`Next.js bundle budget passed for ${result.routes.length} pages (runtime + layout + loading + page entries).`);
}
