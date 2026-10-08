import { proxyAiSettings } from "@/modules/settings/ai-proxy";

export const maxDuration = 180;

/** @deprecated Prefer POST /ai-settings?scope=… */
export function POST(request: Request) {
  return proxyAiSettings(request, "POST");
}
