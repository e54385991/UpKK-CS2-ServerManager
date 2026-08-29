import { getTranslations } from "next-intl/server";
import { TriangleAlert } from "lucide-react";
import { listScheduledTasks } from "@/modules/schedule/api";
import { ScheduleConsole } from "@/modules/schedule/schedule-console";
import { Card } from "@/shared/ui/card";
import { Skeleton } from "@/shared/ui/skeleton";

export async function SchedulePanel({ serverId }: { serverId: number }) {
  const t = await getTranslations("schedule");
  const result = await listScheduledTasks(serverId);
  if (!result.ok) {
    return (
      <Card className="flex items-center gap-3 border-warn/30 bg-warn-muted/40 px-5 py-4 text-sm text-warn">
        <TriangleAlert className="size-4 shrink-0" />
        <span>{t("fetchError", { status: result.status || "network" })}</span>
      </Card>
    );
  }
  return <ScheduleConsole serverId={serverId} initial={result.data} />;
}

export function SchedulePanelSkeleton() {
  return (
    <div className="max-w-2xl space-y-3 rounded-lg border border-line bg-surface p-5 shadow-panel">
      <Skeleton className="h-4 w-40" />
      <Skeleton className="h-10 w-full" />
    </div>
  );
}
