import type { Locale } from "./config";

// Root-layout failures cannot depend on its message provider or API requests.
export const recoveryMessages = {
  "en-US": {
    errorTitle: "Unable to display this page",
    errorDescription: "Try again to load the latest data, or return to the overview.",
    retry: "Try again",
    notFoundTitle: "Page not found",
    notFoundDescription: "This page does not exist or is no longer available.",
    overview: "Back to overview",
  },
  "zh-CN": {
    errorTitle: "暂时无法显示此页面",
    errorDescription: "请重试以重新加载最新数据，或返回总览。",
    retry: "重试",
    notFoundTitle: "页面不存在",
    notFoundDescription: "此页面不存在或已不可用。",
    overview: "返回总览",
  },
} as const satisfies Record<Locale, Record<string, string>>;
