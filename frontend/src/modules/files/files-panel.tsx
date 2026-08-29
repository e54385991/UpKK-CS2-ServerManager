import { getTranslations } from "next-intl/server";
import { Folder, TriangleAlert } from "lucide-react";
import { getFilesWorkspace } from "@/modules/files/api";
import { FilesConsole } from "@/modules/files/files-console";
import { Card } from "@/shared/ui/card";
import { Skeleton } from "@/shared/ui/skeleton";

export async function FilesPanel({
  serverId,
  path,
}: {
  serverId: number;
  path?: string;
}) {
  const t = await getTranslations("files");
  const result = await getFilesWorkspace(serverId, path);
  if (!result.ok) {
    return (
      <Card className="flex items-center gap-3 border-warn/30 bg-warn-muted/40 px-5 py-4 text-sm text-warn">
        <TriangleAlert className="size-4 shrink-0" />
        <span>{t("fetchError", { status: result.status || "network" })}</span>
      </Card>
    );
  }
  return <FilesConsole initial={result.data} />;
}

export function FilesPanelSkeleton() {
  return (
    <div className="space-y-4">
      <div className="rounded-lg border border-line bg-surface p-5 shadow-panel">
        <div className="mb-4 flex items-center gap-2">
          <Folder className="size-4 text-fg-subtle" />
          <Skeleton className="h-4 w-32" />
        </div>
        <Skeleton className="mb-2 h-8 w-full" />
        <Skeleton className="h-8 w-2/3" />
      </div>
    </div>
  );
}
