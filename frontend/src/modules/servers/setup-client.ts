import type { ApiResult } from "@/shared/api/server-fetch";
import type { AutoSetupInput, AutoSetupResult } from "@/modules/servers/setup-api";

type AutoSetupResultDto = {
  success: boolean;
  message: string;
  cs2_username: string;
  cs2_password: string;
  game_directory: string;
  logs?: string[];
  initialized_server_id?: string | null;
};

export async function runAutoSetupFromBrowser(
  input: AutoSetupInput & { sessionId: string },
): Promise<ApiResult<AutoSetupResult>> {
  try {
    const response = await fetch("/setup-stream/run", {
      method: "POST",
      headers: {
        accept: "application/json",
        "content-type": "application/json",
      },
      body: JSON.stringify({
        name: input.name,
        host: input.host,
        ssh_port: input.sshPort,
        ssh_user: input.sshUser,
        ssh_password: input.sshPassword,
        sudo_password: input.sudoPassword || undefined,
        cs2_username: input.cs2Username,
        captcha_token: input.captchaToken,
        captcha_code: input.captchaCode,
        save_config: input.saveConfig,
        open_game_ports: input.openGamePorts,
        session_id: input.sessionId,
      }),
    });
    if (!response.ok) {
      return {
        ok: false,
        status: response.status,
        error: await readBrowserApiError(response),
      };
    }
    const data = (await response.json()) as AutoSetupResultDto;
    return {
      ok: true,
      data: {
        success: data.success,
        message: data.message,
        cs2Username: data.cs2_username,
        cs2Password: data.cs2_password,
        gameDirectory: data.game_directory,
        logs: data.logs ?? [],
        initializedServerId: data.initialized_server_id ?? null,
      },
    };
  } catch (error) {
    return {
      ok: false,
      status: 0,
      error: error instanceof Error ? error.message : "network error",
    };
  }
}

async function readBrowserApiError(response: Response): Promise<string> {
  const fallback = `Request failed with ${response.status}`;
  try {
    const body = (await response.json()) as { detail?: unknown };
    if (typeof body.detail === "string" && body.detail.trim()) {
      return body.detail;
    }
    if (Array.isArray(body.detail) && body.detail.length > 0) {
      const first = body.detail[0] as { msg?: unknown };
      if (typeof first?.msg === "string" && first.msg.trim()) {
        return first.msg;
      }
    }
  } catch {
    // Keep the status fallback when the body is not JSON.
  }
  return fallback;
}
