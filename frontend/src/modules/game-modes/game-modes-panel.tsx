import { getTranslations } from "next-intl/server";
import { TriangleAlert } from "lucide-react";
import { getGameModeCatalog } from "@/modules/game-modes/api";
import { GameModesWizard } from "@/modules/game-modes/game-modes-wizard";
import { getServer } from "@/modules/servers/api";
import { Card } from "@/shared/ui/card";
import { Skeleton } from "@/shared/ui/skeleton";

export async function GameModesPanel({ serverId }: { serverId: number }) {
  const t = await getTranslations("gameModes");
  const [catalog, server] = await Promise.all([
    getGameModeCatalog(serverId),
    getServer(serverId),
  ]);
  if (!catalog.ok) {
    return (
      <Card className="flex items-center gap-3 border-warn/30 bg-warn-muted/40 px-5 py-4 text-sm text-warn">
        <TriangleAlert className="size-4 shrink-0" />
        <span>{t("fetchError", { status: catalog.status || "network" })}</span>
      </Card>
    );
  }
  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-lg font-semibold text-fg">{t("title")}</h2>
        <p className="mt-1 text-sm text-fg-muted">{t("pageHelp")}</p>
      </div>
      <GameModesWizard
        catalog={catalog.data}
        serverName={server.ok ? server.data.name : `#${serverId}`}
      />
    </div>
  );
}

export function GameModesPanelSkeleton() {
  return (
    <div className="space-y-4">
      <Skeleton className="h-6 w-56" />
      <Skeleton className="h-16 w-full" />
      <Skeleton className="h-64 w-full" />
      <Skeleton className="h-40 w-full" />
    </div>
  );
}
