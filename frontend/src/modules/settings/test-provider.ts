import { testProfileAiProvider, testSystemAiProvider } from "@/modules/settings/ai-settings-client";

/** @deprecated Use testSystemAiProvider / testProfileAiProvider. */
export async function testAiProvider(scope: "profile" | "system") {
  return scope === "profile" ? testProfileAiProvider() : testSystemAiProvider();
}
