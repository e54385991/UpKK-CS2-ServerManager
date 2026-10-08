import { proxyAssistant } from "@/modules/assistant/assistant-proxy";

export const maxDuration = 30;

export function GET(request: Request) {
  return proxyAssistant(request);
}

export function POST(request: Request) {
  return proxyAssistant(request);
}
