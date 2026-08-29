"use server";

import type { ApiResult } from "@/shared/api/server-fetch";
import type { ActionResultDto, SessionUserDto } from "@/shared/api/types";
import {
  completePasswordReset,
  registerAccount,
  requestPasswordReset,
} from "@/modules/auth/api";

export async function registerAccountAction(input: {
  readonly username: string;
  readonly email: string;
  readonly password: string;
  readonly captchaToken: string;
  readonly captchaCode: string;
}): Promise<ApiResult<SessionUserDto>> {
  return registerAccount(input);
}

export async function requestPasswordResetAction(input: {
  readonly email: string;
  readonly captchaToken: string;
  readonly captchaCode: string;
}): Promise<ApiResult<ActionResultDto>> {
  return requestPasswordReset(input);
}

export async function completePasswordResetAction(input: {
  readonly token: string;
  readonly newPassword: string;
}): Promise<ApiResult<ActionResultDto>> {
  return completePasswordReset(input);
}
