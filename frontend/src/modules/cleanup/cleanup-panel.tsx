import { getTranslations } from "next-intl/server";
import { getCleanupPolicy } from "@/modules/cleanup/api";
import { CleanupConsole, CleanupPanelSkeleton } from "@/modules/cleanup/cleanup-console";
import { Card } from "@/shared/ui/card";

export async function CleanupPanel({ serverId }: { serverId: number }) {
  const t = await getTranslations("cleanup");
  const policy = await getCleanupPolicy(serverId);
  if (!policy.ok && policy.status === 404) {
    return (
      <Card className="border-warn/30 bg-warn-muted/40 px-5 py-4 text-sm text-warn">
        {t("fetchError", { status: "404" })}
      </Card>
    );
  }
  return (
    <CleanupConsole
      serverId={serverId}
      initialPolicy={policy.ok ? policy.data : null}
    />
  );
}

export { CleanupPanelSkeleton };
