import { getTranslations } from "next-intl/server";
import { TriangleAlert } from "lucide-react";
import { getFilesWorkspace } from "@/modules/files/api";
import { FilesConsole } from "@/modules/files/files-console-loader";
import { Card } from "@/shared/ui/card";
export { FilesPanelSkeleton } from "./files-panel-skeleton";

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
