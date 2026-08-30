import { getRequestConfig } from "next-intl/server";
import { cookies, headers } from "next/headers";
import { DEFAULT_LOCALE, LOCALE_COOKIE, LOCALES, isLocale } from "@/i18n/config";

/**
 * i18n without URL routing: the active locale comes from the `locale` cookie,
 * falling back to the browser's Accept-Language, then the default. This keeps
 * URLs clean (no /[locale] segment) while remaining SSR-authoritative.
 */
export default getRequestConfig(async () => {
  const cookieLocale = (await cookies()).get(LOCALE_COOKIE)?.value;

  let locale = isLocale(cookieLocale) ? cookieLocale : undefined;

  if (!locale) {
    const accept = (await headers()).get("accept-language") ?? "";
    locale = LOCALES.find((candidate) =>
      accept.toLowerCase().includes(candidate.toLowerCase()),
    );
    if (!locale && accept.toLowerCase().includes("zh")) locale = "zh-CN";
    if (!locale && accept.toLowerCase().includes("en")) locale = "en-US";
  }

  const active = locale ?? DEFAULT_LOCALE;
  const messages = (await import(`@/i18n/messages/${active}.json`)).default;

  return { locale: active, messages };
});
