import { getTranslations } from "next-intl/server";
import { SquareTerminal, TriangleAlert } from "lucide-react";
import { getConsolePane, getConsoleWorkspace } from "@/modules/console/api";
import {
  ConsoleLauncherView,
  FocusedConsoleView,
} from "@/modules/console/console-workspace";
import type { ConsoleKind } from "@/modules/console/types";
import { Card } from "@/shared/ui/card";
import { Skeleton } from "@/shared/ui/skeleton";

export async function ConsolePanel({
  serverId,
  focus,
}: {
  serverId: number;
  focus?: ConsoleKind;
}) {
  const t = await getTranslations("console");
  const [result, gamePane] = await Promise.all([
    getConsoleWorkspace(serverId),
    focus === "game" ? getConsolePane(serverId, "game") : Promise.resolve(null),
  ]);
  if (!result.ok) {
    return (
      <Card className="flex items-center gap-3 border-warn/30 bg-warn-muted/40 px-5 py-4 text-sm text-warn">
        <TriangleAlert className="size-4 shrink-0" />
        <span>{t("fetchError", { status: result.status || "network" })}</span>
      </Card>
    );
  }
  if (focus === "ssh" || focus === "game") {
    return (
      <FocusedConsoleView
        initial={result.data}
        kind={focus}
        seedPane={gamePane && gamePane.ok ? gamePane.data : null}
      />
    );
  }
  return <ConsoleLauncherView initial={result.data} />;
}

export function ConsolePanelSkeleton() {
  return (
    <div className="space-y-4">
      <div className="rounded-lg border border-line bg-surface p-5 shadow-panel">
        <div className="mb-4 flex items-center gap-2">
          <SquareTerminal className="size-4 text-fg-subtle" />
          <Skeleton className="h-4 w-32" />
        </div>
        <Skeleton className="h-40 w-full" />
      </div>
    </div>
  );
}
