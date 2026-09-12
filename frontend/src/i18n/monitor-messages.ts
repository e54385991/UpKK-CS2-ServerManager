import type { AbstractIntlMessages } from "next-intl";

export async function withMonitorMessages(
  locale: string,
  messages: AbstractIntlMessages,
): Promise<AbstractIntlMessages> {
  const catalog =
    locale === "zh-CN"
      ? (await import("@/i18n/messages/monitor/zh-CN.json")).default
      : (await import("@/i18n/messages/monitor/en-US.json")).default;
  const root = messages as { settings?: Record<string, unknown> };
  return {
    ...messages,
    settings: { ...(root.settings ?? {}), monitor: catalog },
  };
}
