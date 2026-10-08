import "server-only";
import { cache } from "react";
import { headers } from "next/headers";
import { redirect } from "@/i18n/redirect";
import { loginReturnHref } from "@/i18n/paths";
import type { PublicRoute } from "@/i18n/public-route";
import { getSession as loadSession } from "@/modules/auth/session";

/** RSC render-scoped only. Actions and Route Handlers keep the uncached loader. */
export const getSession = cache(loadSession);

export async function loginReturnPath(): Promise<PublicRoute> {
  const publicPath = (await headers()).get("x-upkk-public-path") ?? "/overview";
  return loginReturnHref(publicPath) as PublicRoute;
}

export async function requireSession() {
  const session = await getSession();
  if (!session) redirect(await loginReturnPath());
  return session;
}
