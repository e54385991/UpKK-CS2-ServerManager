import type enUsMessages from "@/i18n/messages/en-US.json";
import type { Locale } from "@/i18n/config";

declare module "next-intl" {
  interface AppConfig {
    Locale: Locale;
    Messages: typeof enUsMessages;
  }
}
