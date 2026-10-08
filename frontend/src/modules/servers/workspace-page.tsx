import { Suspense, type ReactNode } from "react";
import { getTranslations } from "next-intl/server";
import { SessionGate } from "@/modules/shell/session-slots";
import type { ServerWorkspaceCategory } from "./workspace";

export async function WorkspacePage({ category, fallback, children }: {
  category: ServerWorkspaceCategory;
  fallback: ReactNode;
  children: ReactNode;
}) {
  const t = await getTranslations("serverWorkspace");
  return <section data-route={`/servers/[id]${category === "overview" ? "" : `/${category}`}`} className="space-y-4">
    <Suspense fallback={null}><SessionGate /></Suspense>
    <h2 className="text-base font-semibold text-fg" data-testid="route-heading">{t(`categories.${category}`)}</h2>
    <Suspense fallback={fallback}>{children}</Suspense>
  </section>;
}
