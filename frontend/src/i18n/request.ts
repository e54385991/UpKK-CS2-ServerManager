import { getRequestConfig } from "next-intl/server";
import { locale as rootLocale } from "next/root-params";
import { notFound } from "next/navigation";
import { isLocale } from "@/i18n/config";
import { withMonitorMessages } from "@/i18n/monitor-messages";

// Proxy resolves the existing cookie / Accept-Language policy into a root
// parameter. Only local dictionaries are shared; API data remains no-store.
export default getRequestConfig(async ({ locale }) => {
  const active = locale ?? await rootLocale();
  if (!isLocale(active)) notFound();
  const messages = (await import(`@/i18n/messages/${active}.json`)).default;
  return { locale: active, messages: await withMonitorMessages(active, messages) };
});
