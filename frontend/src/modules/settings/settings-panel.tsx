import { getTranslations } from "next-intl/server";
import { TriangleAlert } from "lucide-react";
import { getAiSettings, getSettings } from "@/modules/settings/api";
import { AiSettingsForm } from "@/modules/settings/ai-settings-form";
import { DownloadCacheCard } from "@/modules/settings/download-cache-card";
import { SettingsForm } from "@/modules/settings/settings-form";
import { SettingsSection } from "@/modules/settings/settings-section";
import { Card } from "@/shared/ui/card";
import { Skeleton } from "@/shared/ui/skeleton";

const SETTINGS_SECTIONS = [
  { id: "settings-downloads", key: "downloads" },
  { id: "settings-notifications", key: "notifications" },
  { id: "settings-security", key: "security" },
  { id: "settings-logging", key: "logging" },
  { id: "settings-ai", key: "ai" },
] as const;

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
      <nav
        aria-label={t("categoryNavLabel")}
        data-testid="settings-category-nav"
        className="sticky top-2 z-20 -mx-1 overflow-x-auto rounded-lg border border-line bg-canvas/95 p-1 shadow-panel backdrop-blur-md"
      >
        <div className="flex min-w-max gap-1">
          {SETTINGS_SECTIONS.map((section) => (
            <a
              key={section.id}
              href={`#${section.id}`}
              className="rounded-md px-3 py-2 text-sm font-medium text-fg-muted transition-colors hover:bg-surface-raised hover:text-fg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/60"
            >
              {t(`categories.${section.key}`)}
            </a>
          ))}
        </div>
      </nav>

      <SettingsForm initial={result.data} />
      <SettingsSection
        id="settings-download-cache"
        title={t("sections.downloadCache.title")}
        description={t("sections.downloadCache.description")}
        testId="settings-section-download-cache"
      >
        <DownloadCacheCard initial={result.data} />
      </SettingsSection>
      <SettingsSection
        id="settings-ai"
        title={t("sections.ai.title")}
        description={t("sections.ai.description")}
        testId="settings-section-ai"
      >
        <AiSettingsForm initial={ai.ok ? ai.data : null} />
      </SettingsSection>
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
