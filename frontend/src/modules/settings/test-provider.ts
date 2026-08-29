import type { AssistantProviderTestViewDto } from "@/shared/api/types";

type TestResult =
  | { readonly ok: true; readonly data: AssistantProviderTestViewDto }
  | { readonly ok: false; readonly status: number; readonly error: string };

export async function testAiProvider(
  scope: "profile" | "system",
): Promise<TestResult> {
  try {
    const response = await fetch(`/ai-provider-test?scope=${scope}`, {
      method: "POST",
      headers: {
        accept: "application/json",
        "content-type": "application/json",
      },
      body: "{}",
    });
    if (!response.ok) {
      return {
        ok: false,
        status: response.status,
        error: await readError(response),
      };
    }
    return {
      ok: true,
      data: (await response.json()) as AssistantProviderTestViewDto,
    };
  } catch (error) {
    return {
      ok: false,
      status: 0,
      error: error instanceof Error ? error.message : "network error",
    };
  }
}

async function readError(response: Response): Promise<string> {
  const fallback = `Request failed with ${response.status}`;
  try {
    const body = (await response.json()) as { detail?: unknown; message?: unknown };
    if (typeof body.detail === "string" && body.detail.trim()) {
      return body.detail;
    }
    if (typeof body.message === "string" && body.message.trim()) {
      return body.message;
    }
  } catch {
    // optional JSON body
  }
  return fallback;
}
