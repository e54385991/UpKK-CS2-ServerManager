import { getTranslations } from "next-intl/server";
import { getServer } from "@/modules/servers/api";
import { HelpConsole } from "@/modules/help/help-console";
import { Card } from "@/shared/ui/card";
import { Skeleton } from "@/shared/ui/skeleton";

export async function HelpPanel({ serverId }: { serverId: number }) {
  const t = await getTranslations("serverHelp");
  const result = await getServer(serverId);
  if (!result.ok) {
    return (
      <Card className="border-warn/30 bg-warn-muted/40 px-5 py-4 text-sm text-warn">
        {t("fetchError", { status: result.status || "network" })}
      </Card>
    );
  }
  return (
    <HelpConsole
      host={result.data.host}
      gamePort={result.data.gamePort}
      gameDirectory={result.data.gameDirectory}
    />
  );
}

export function HelpPanelSkeleton() {
  return (
    <div className="max-w-3xl space-y-3">
      <Skeleton className="h-20 w-full rounded-lg" />
      <Skeleton className="h-64 w-full rounded-lg" />
    </div>
  );
}
