import { proxyAssistant } from "@/modules/assistant/assistant-proxy";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";
export const maxDuration = 30;

export function GET(request: Request) {
  return proxyAssistant(request);
}

export function POST(request: Request) {
  return proxyAssistant(request);
}
