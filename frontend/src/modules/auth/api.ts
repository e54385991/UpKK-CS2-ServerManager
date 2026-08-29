import "server-only";
import { apiFetch, type ApiResult } from "@/shared/api/server-fetch";
import type { ActionResultDto, SessionUserDto } from "@/shared/api/types";

export function registerAccount(input: {
  readonly username: string;
  readonly email: string;
  readonly password: string;
  readonly captchaToken: string;
  readonly captchaCode: string;
}): Promise<ApiResult<SessionUserDto>> {
  return apiFetch<SessionUserDto>("/api/v1/auth/register", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      username: input.username,
      email: input.email,
      password: input.password,
      captcha_token: input.captchaToken,
      captcha_code: input.captchaCode,
    }),
  });
}

export function requestPasswordReset(input: {
  readonly email: string;
  readonly captchaToken: string;
  readonly captchaCode: string;
}): Promise<ApiResult<ActionResultDto>> {
  return apiFetch<ActionResultDto>("/api/v1/auth/forgot-password", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      email: input.email,
      captcha_token: input.captchaToken,
      captcha_code: input.captchaCode,
    }),
  });
}

export function completePasswordReset(input: {
  readonly token: string;
  readonly newPassword: string;
}): Promise<ApiResult<ActionResultDto>> {
  return apiFetch<ActionResultDto>("/api/v1/auth/reset-password", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      token: input.token,
      new_password: input.newPassword,
    }),
  });
}
