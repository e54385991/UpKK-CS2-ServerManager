import { getRequestConfig } from "next-intl/server";
import { cookies, headers } from "next/headers";
import { LOCALE_COOKIE, resolveLocale } from "@/i18n/config";

/**
 * i18n without URL routing: the active locale comes from the `locale` cookie,
 * then from the browser's highest-priority Accept-Language value. Chinese
 * resolves to zh-CN; every other value defaults to en-US. This keeps URLs clean
 * (no /[locale] segment) while remaining SSR-authoritative.
 */
export default getRequestConfig(async () => {
  const [cookieStore, incoming] = await Promise.all([cookies(), headers()]);
  const cookieLocale = cookieStore.get(LOCALE_COOKIE)?.value;
  const active = resolveLocale(
    cookieLocale,
    incoming.get("accept-language"),
  );
  const messages = (await import(`@/i18n/messages/${active}.json`)).default;

  return { locale: active, messages };
});
