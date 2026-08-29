import { getTranslations } from "next-intl/server";
import { Map as MapIcon, TriangleAlert } from "lucide-react";
import { getMapsWorkspace } from "@/modules/maps/api";
import { MapsConsole } from "@/modules/maps/maps-console";
import { Card } from "@/shared/ui/card";
import { Skeleton } from "@/shared/ui/skeleton";

export async function MapsPanel({ serverId }: { serverId: number }) {
  const t = await getTranslations("maps");
  const result = await getMapsWorkspace(serverId);
  if (!result.ok) {
    return (
      <Card className="flex items-center gap-3 border-warn/30 bg-warn-muted/40 px-5 py-4 text-sm text-warn">
        <TriangleAlert className="size-4 shrink-0" />
        <span>{t("fetchError", { status: result.status || "network" })}</span>
      </Card>
    );
  }
  return <MapsConsole initial={result.data} />;
}

export function MapsPanelSkeleton() {
  return (
    <div className="space-y-4">
      <div className="rounded-lg border border-line bg-surface p-5 shadow-panel">
        <div className="mb-4 flex items-center gap-2">
          <MapIcon className="size-4 text-fg-subtle" />
          <Skeleton className="h-4 w-32" />
        </div>
        <Skeleton className="mb-2 h-8 w-full" />
        <Skeleton className="h-8 w-2/3" />
      </div>
      <div className="rounded-lg border border-line bg-surface p-5 shadow-panel">
        <Skeleton className="mb-3 h-4 w-40" />
        <Skeleton className="h-24 w-full" />
      </div>
    </div>
  );
}
