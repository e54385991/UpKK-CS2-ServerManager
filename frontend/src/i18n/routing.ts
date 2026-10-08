import { defineRouting } from "next-intl/routing";
import { DEFAULT_LOCALE, LOCALES, LOCALE_COOKIE } from "./config";

export const routing = defineRouting({
  locales: LOCALES,
  defaultLocale: DEFAULT_LOCALE,
  localePrefix: "never",
  // Proxy applies the existing quality-based Accept-Language policy.
  localeDetection: false,
  alternateLinks: false,
  localeCookie: { name: LOCALE_COOKIE, sameSite: "lax", maxAge: 365 * 24 * 60 * 60 },
});
