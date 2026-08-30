import { getTranslations } from "next-intl/server";
import { FileCode, TriangleAlert } from "lucide-react";
import { getPluginConfigSources } from "@/modules/plugin-configs/api";
import { PluginConfigsConsole } from "@/modules/plugin-configs/plugin-configs-console";
import { Card } from "@/shared/ui/card";
import { Skeleton } from "@/shared/ui/skeleton";

export async function PluginConfigsPanel({ serverId }: { serverId: number }) {
  const t = await getTranslations("pluginConfigs");
  const result = await getPluginConfigSources(serverId);
  if (!result.ok) {
    return (
      <Card className="flex items-center gap-3 border-warn/30 bg-warn-muted/40 px-5 py-4 text-sm text-warn">
        <TriangleAlert className="size-4 shrink-0" />
        <span>{t("fetchError", { status: result.status || "network" })}</span>
      </Card>
    );
  }
  return <PluginConfigsConsole initial={result.data} />;
}

export function PluginConfigsPanelSkeleton() {
  return (
    <div className="space-y-4">
      <div className="rounded-lg border border-line bg-surface p-5 shadow-panel">
        <div className="mb-4 flex items-center gap-2">
          <FileCode className="size-4 text-fg-subtle" />
          <Skeleton className="h-4 w-40" />
        </div>
        <Skeleton className="mb-2 h-8 w-full" />
        <Skeleton className="h-8 w-2/3" />
      </div>
      <div className="grid gap-4 lg:grid-cols-3">
        <div className="rounded-lg border border-line bg-surface p-5 shadow-panel">
          <Skeleton className="mb-3 h-4 w-28" />
          <Skeleton className="h-32 w-full" />
        </div>
        <div className="rounded-lg border border-line bg-surface p-5 shadow-panel">
          <Skeleton className="mb-3 h-4 w-20" />
          <Skeleton className="h-32 w-full" />
        </div>
        <div className="rounded-lg border border-line bg-surface p-5 shadow-panel">
          <Skeleton className="mb-3 h-4 w-24" />
          <Skeleton className="h-32 w-full" />
        </div>
      </div>
    </div>
  );
}
