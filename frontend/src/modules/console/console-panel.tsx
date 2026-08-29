import { getTranslations } from "next-intl/server";
import { SquareTerminal, TriangleAlert } from "lucide-react";
import { getConsoleWorkspace } from "@/modules/console/api";
import { ConsoleWorkspaceView } from "@/modules/console/console-workspace";
import { Card } from "@/shared/ui/card";
import { Skeleton } from "@/shared/ui/skeleton";

export async function ConsolePanel({ serverId }: { serverId: number }) {
  const t = await getTranslations("console");
  const result = await getConsoleWorkspace(serverId);
  if (!result.ok) {
    return (
      <Card className="flex items-center gap-3 border-warn/30 bg-warn-muted/40 px-5 py-4 text-sm text-warn">
        <TriangleAlert className="size-4 shrink-0" />
        <span>{t("fetchError", { status: result.status || "network" })}</span>
      </Card>
    );
  }
  return <ConsoleWorkspaceView initial={result.data} />;
}

export function ConsolePanelSkeleton() {
  return (
    <div className="space-y-4">
      <div className="rounded-lg border border-line bg-surface p-5 shadow-panel">
        <div className="mb-4 flex items-center gap-2">
          <SquareTerminal className="size-4 text-fg-subtle" />
          <Skeleton className="h-4 w-32" />
        </div>
        <Skeleton className="h-80 w-full" />
      </div>
    </div>
  );
}
