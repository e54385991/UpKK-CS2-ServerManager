import { getTranslations } from "next-intl/server";
import { TriangleAlert } from "lucide-react";
import { getAiSettings, getSettings } from "@/modules/settings/api";
import { AiSettingsForm } from "@/modules/settings/ai-settings-form";
import { SettingsForm } from "@/modules/settings/settings-form";
import { Card } from "@/shared/ui/card";
import { Skeleton } from "@/shared/ui/skeleton";

export async function SettingsPanel() {
  const t = await getTranslations("settings");
  const [result, ai] = await Promise.all([getSettings(), getAiSettings()]);

  if (!result.ok) {
    const forbidden = result.status === 403;
    return (
      <Card className="flex items-center gap-3 border-warn/30 bg-warn-muted/40 px-5 py-4 text-sm text-warn">
        <TriangleAlert className="size-4 shrink-0" />
        <span>
          {forbidden
            ? t("forbidden")
            : t("fetchError", { status: result.status || "network" })}
        </span>
      </Card>
    );
  }

  return (
    <div className="space-y-6">
      <SettingsForm initial={result.data} />
      {ai.ok ? <AiSettingsForm initial={ai.data} /> : null}
    </div>
  );
}

export function SettingsPanelSkeleton() {
  return (
    <div className="grid gap-6 xl:grid-cols-2">
      <CardSkeleton />
      <CardSkeleton />
    </div>
  );
}

function CardSkeleton() {
  return (
    <div className="rounded-lg border border-line bg-surface p-5 shadow-panel">
      <div className="mb-5 flex items-center gap-3">
        <Skeleton className="size-9 rounded-md" />
        <div className="space-y-2">
          <Skeleton className="h-4 w-32" />
          <Skeleton className="h-3 w-56" />
        </div>
      </div>
      <div className="space-y-4">
        <Skeleton className="h-10 w-full" />
        <Skeleton className="h-10 w-full" />
        <Skeleton className="h-24 w-full" />
      </div>
    </div>
  );
}
