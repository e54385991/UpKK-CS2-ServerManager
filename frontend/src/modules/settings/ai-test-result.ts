import type { AlertOptions } from "@/shared/feedback";
import type { AssistantProviderTestViewDto } from "@/shared/api/types";

type TranslateKey =
  | "testOk"
  | "testFail"
  | "testUsableTitle"
  | "testUnusableTitle"
  | "testText"
  | "testTools"
  | "testStream"
  | "testUsableHelp"
  | "testUnusableHelp"
  | "testAck";

type Translate = (key: TranslateKey) => string;

export function providerTestAlert(
  data: AssistantProviderTestViewDto,
  t: Translate,
): AlertOptions {
  const usable = data.success;
  const mark = (ok: boolean) => (ok ? t("testOk") : t("testFail"));
  return {
    title: usable ? t("testUsableTitle") : t("testUnusableTitle"),
    description: [
      `${t("testText")}${mark(data.text_response_ok)}`,
      `${t("testTools")}${mark(data.tool_calling_ok)}`,
      `${t("testStream")}${mark(data.streaming_ok)}`,
      "",
      usable ? t("testUsableHelp") : t("testUnusableHelp"),
      data.message?.trim() ? data.message.trim() : "",
    ]
      .filter((line, index, lines) => line !== "" || (index > 0 && lines[index - 1] !== ""))
      .join("\n")
      .trim(),
    tone: usable ? "ok" : "danger",
    confirmLabel: t("testAck"),
  };
}

export function providerTestErrorAlert(error: string, t: Translate): AlertOptions {
  return {
    title: t("testUnusableTitle"),
    description: [t("testUnusableHelp"), error].filter(Boolean).join("\n\n"),
    tone: "danger",
    confirmLabel: t("testAck"),
  };
}
