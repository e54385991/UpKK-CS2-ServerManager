import { proxyAiSettings } from "@/modules/settings/ai-proxy";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";
export const maxDuration = 180;

export function GET(request: Request) {
  return proxyAiSettings(request, "GET");
}

export function PUT(request: Request) {
  return proxyAiSettings(request, "PUT");
}

export function POST(request: Request) {
  return proxyAiSettings(request, "POST");
}
