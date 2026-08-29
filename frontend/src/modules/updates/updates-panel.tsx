import { getTranslations } from "next-intl/server";
import { TriangleAlert } from "lucide-react";
import { listCustomCommands } from "@/modules/commands/api";
import { getCurrentServerOperation } from "@/modules/servers/api";
import {
  getGameUpdates,
  getPluginUpdates,
  listRegisterMarketOptions,
} from "@/modules/updates/api";
import { GameUpdatesConsole } from "@/modules/updates/game-updates-console";
import { UpdatesConsole } from "@/modules/updates/updates-console";
import { Card } from "@/shared/ui/card";
import { Skeleton } from "@/shared/ui/skeleton";

export async function UpdatesPanel({ serverId }: { serverId: number }) {
  const tGame = await getTranslations("gameUpdates");
  const tPlugins = await getTranslations("pluginUpdates");
  const [game, plugins, current, commands, market] = await Promise.all([
    getGameUpdates(serverId),
    getPluginUpdates(serverId),
    getCurrentServerOperation(serverId),
    listCustomCommands(serverId),
    listRegisterMarketOptions(),
  ]);

  return (
    <div className="space-y-6">
      {game.ok ? (
        <GameUpdatesConsole
          serverId={serverId}
          initial={game.data}
          currentOperation={current.ok ? current.data : null}
        />
      ) : (
        <Card className="flex items-center gap-3 border-warn/30 bg-warn-muted/40 px-5 py-4 text-sm text-warn">
          <TriangleAlert className="size-4 shrink-0" />
          <span>{tGame("fetchError", { status: game.status || "network" })}</span>
        </Card>
      )}

      {plugins.ok ? (
        <UpdatesConsole
          serverId={serverId}
          initial={plugins.data}
          savedCommands={commands.ok ? commands.data : []}
          marketOptions={market.ok ? market.data : []}
        />
      ) : (
        <Card className="flex items-center gap-3 border-warn/30 bg-warn-muted/40 px-5 py-4 text-sm text-warn">
          <TriangleAlert className="size-4 shrink-0" />
          <span>
            {tPlugins("fetchError", { status: plugins.status || "network" })}
          </span>
        </Card>
      )}
    </div>
  );
}

export function UpdatesPanelSkeleton() {
  return (
    <div className="max-w-3xl space-y-6">
      <div className="space-y-3 rounded-lg border border-line bg-surface p-5 shadow-panel">
        <Skeleton className="h-4 w-40" />
        <Skeleton className="h-10 w-full" />
        <Skeleton className="h-10 w-full" />
      </div>
      <div className="space-y-3 rounded-lg border border-line bg-surface p-5 shadow-panel">
        <Skeleton className="h-4 w-40" />
        <Skeleton className="h-10 w-full" />
      </div>
    </div>
  );
}
