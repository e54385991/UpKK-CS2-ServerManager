import nextCoreWebVitals from "eslint-config-next/core-web-vitals";

/**
 * Flat ESLint config. `eslint-config-next/core-web-vitals` already ships a flat
 * config array (Next plugin + React/hooks + import + jsx-a11y + the TypeScript
 * parser), so it is spread directly — no `FlatCompat` shim needed on ESLint 10.
 */
const eslintConfig = [
  ...nextCoreWebVitals,
  {
    ignores: [
      ".next/**",
      "node_modules/**",
      "src/shared/api/schema.d.ts",
      "e2e/**",
      "playwright.config.ts",
    ],
  },
  {
    rules: {
      // Enforce the app → modules → shared dependency direction. Domain modules
      // and shared code must not import from the app router tree.
      "no-restricted-imports": [
        "error",
        {
          patterns: [
            {
              group: ["@/app/*", "**/app/*"],
              message:
                "Domain modules and shared code must not import from the app router layer.",
            },
          ],
        },
      ],
    },
  },
];

export default eslintConfig;
