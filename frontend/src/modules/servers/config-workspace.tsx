import { notFound } from "next/navigation";
import { getTranslations } from "next-intl/server";
import {
  ServerConfigWorkspace,
  type ServerConfigSection,
} from "@/modules/servers/config-form";
import { getServer } from "@/modules/servers/render-queries";
import { Card } from "@/shared/ui/card";
import { Skeleton } from "@/shared/ui/skeleton";

export async function ServerConfigSection({
  serverId,
  section,
}: {
  serverId: number;
  section: ServerConfigSection;
}) {
  const [t, result] = await Promise.all([
    getTranslations("serverDetail"),
    getServer(serverId),
  ]);
  if (!result.ok && result.status === 404) notFound();
  if (!result.ok) {
    return (
      <Card className="border-warn/30 bg-warn-muted/40 px-5 py-4 text-sm text-warn">
        {t("fetchError", { status: result.status || "network" })}
      </Card>
    );
  }

  return <ServerConfigWorkspace server={result.data} section={section} />;
}

export function ServerConfigSkeleton({
  section = "game",
}: {
  section?: ServerConfigSection;
}) {
  return (
    <div
      className="max-w-3xl space-y-6"
      data-testid={section === "host" ? "host-config-loading" : "server-config-loading"}
    >
      <div className="flex flex-wrap gap-2">
        <Skeleton className="h-9 w-24" />
        <Skeleton className="h-9 w-24" />
      </div>
      <Skeleton className="h-64 rounded-lg" />
      <Skeleton className="h-40 rounded-lg" />
    </div>
  );
}
