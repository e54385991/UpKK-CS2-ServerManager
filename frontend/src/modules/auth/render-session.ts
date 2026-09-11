import "server-only";
import { cache } from "react";
import { redirect } from "next/navigation";
import { getSession as loadSession } from "@/modules/auth/session";

/** RSC render-scoped only. Actions and Route Handlers keep the uncached loader. */
export const getSession = cache(loadSession);

export async function requireSession() {
  const session = await getSession();
  if (!session) redirect("/login");
  return session;
}
