import { getTranslations } from "next-intl/server";
import { TriangleAlert } from "lucide-react";
import { getAdminAnnouncements } from "@/modules/announcements/api";
import { getAiSettings, getSettings } from "@/modules/settings/api";
import { SettingsWorkspace } from "@/modules/settings/settings-workspace";
import { Card } from "@/shared/ui/card";

export async function SettingsPanel() {
  const t = await getTranslations("settings");
  const [result, ai, announcements] = await Promise.all([
    getSettings(),
    getAiSettings(),
    getAdminAnnouncements(),
  ]);

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
    <SettingsWorkspace
      settings={result.data}
      ai={ai.ok ? ai.data : null}
      announcements={announcements.ok ? announcements.data : []}
      announcementError={announcements.ok ? undefined : announcements.error}
    />
  );
}
