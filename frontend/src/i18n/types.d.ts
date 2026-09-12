import type enUsMessages from "@/i18n/messages/en-US.json";
import type monitorMessages from "@/i18n/messages/monitor/en-US.json";
import type { Locale } from "@/i18n/config";

type SettingsMessages = typeof enUsMessages.settings & {
  monitor: typeof monitorMessages;
};

declare module "next-intl" {
  interface AppConfig {
    Locale: Locale;
    Messages: Omit<typeof enUsMessages, "settings"> & { settings: SettingsMessages };
  }
}
