"use client";

import { useEffect, useMemo, useState } from "react";
import { useTranslations } from "next-intl";
import { SettingsForm, type SettingsSectionKey } from "@/modules/settings/settings-form";
import { DownloadCacheCard } from "@/modules/settings/download-cache-card";
import { AiSettingsForm } from "@/modules/settings/ai-settings-form";
import { SettingsTransferCard } from "@/modules/settings/settings-transfer-card";
import { PerformanceDiagnosticsCard } from "@/modules/settings/diagnostics-card";
import { SettingsSection } from "@/modules/settings/settings-section";
import type { AiSystemSettings, SystemSettings } from "@/modules/settings/types";
import { AnnouncementManager } from "@/modules/settings/announcement-manager";
import type { Announcement } from "@/modules/announcements/types";
import { cn } from "@/shared/lib/cn";

type SectionKey = SettingsSectionKey | "download-cache" | "ai" | "transfer" | "performance" | "announcements";

const SECTIONS = [
  { id: "settings-performance", key: "performance" },
  { id: "settings-announcements", key: "announcements" },
  { id: "settings-downloads", key: "downloads" },
  { id: "settings-download-cache", key: "downloadCache" },
  { id: "settings-notifications", key: "notifications" },
  { id: "settings-security", key: "security" },
  { id: "settings-logging", key: "logging" },
  { id: "settings-ai", key: "ai" },
  { id: "settings-transfer", key: "transfer" },
] as const;

function keyFromHash(hash: string): SectionKey {
  const found = SECTIONS.find((section) => `#${section.id}` === hash);
  if (!found) return "performance";
  return found.key === "downloadCache" ? "download-cache" : (found.key as SectionKey);
}

export function SettingsWorkspace({
  settings,
  ai,
  announcements,
  announcementError,
}: {
  settings: SystemSettings;
  ai: AiSystemSettings | null;
  announcements: readonly Announcement[];
  announcementError?: string;
}) {
  const t = useTranslations("settings");
  const [active, setActive] = useState<SectionKey>(() =>
    typeof window === "undefined" ? "performance" : keyFromHash(window.location.hash),
  );
  const [dirty, setDirty] = useState<ReadonlySet<SectionKey>>(() => new Set());

  useEffect(() => {
    if (!window.location.hash) {
      window.history.replaceState(null, "", `${window.location.pathname}${window.location.search}#settings-performance`);
    }
    const onHash = () => setActive(keyFromHash(window.location.hash));
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  const mainSection = useMemo<SettingsSectionKey>(
    () =>
      active === "downloads" ||
      active === "notifications" ||
      active === "security" ||
      active === "logging"
        ? active
        : "hidden",
    [active],
  );
  const markDirty = (section: SectionKey) => setDirty((current) => new Set(current).add(section));
  const clearDirty = (section: SectionKey) => setDirty((current) => { const next = new Set(current); next.delete(section); return next; });

  function select(section: (typeof SECTIONS)[number]) {
    const next = section.key === "downloadCache" ? "download-cache" : (section.key as SectionKey);
    setActive(next);
    window.history.replaceState(null, "", `${window.location.pathname}${window.location.search}#${section.id}`);
  }

  return (
    <div className="grid gap-6 lg:grid-cols-[14rem_minmax(0,1fr)]">
      <nav
        aria-label={t("categoryNavLabel")}
        data-testid="settings-category-nav"
        className="h-fit rounded-lg border border-line bg-surface p-2 shadow-panel lg:sticky lg:top-4"
      >
        <div className="flex gap-1 overflow-x-auto lg:block lg:space-y-1">
          {SECTIONS.map((section) => {
            const sectionKey = section.key === "downloadCache" ? "download-cache" : (section.key as SectionKey);
            const selected = active === sectionKey;
            return (
              <a
                key={section.id}
                href={`#${section.id}`}
                aria-current={selected ? "page" : undefined}
                data-active={selected ? "true" : undefined}
                onClick={() => select(section)}
                className={cn(
                  "flex min-w-max items-center justify-between gap-2 rounded-md px-3 py-2 text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/60",
                  selected ? "bg-primary-muted text-primary" : "text-fg-muted hover:bg-surface-raised hover:text-fg",
                )}
              >
                <span>{t(`categories.${section.key}`)}</span>
                {dirty.has(sectionKey) ? <span className="text-xs text-warn">{t("unsaved")}</span> : null}
              </a>
            );
          })}
        </div>
      </nav>

      <div className="min-w-0">
        {active === "performance" ? (
          <SettingsSection
            id="settings-performance"
            title={t("sections.performance.title")}
            description={t("sections.performance.description")}
            testId="settings-section-performance"
          >
            <PerformanceDiagnosticsCard />
          </SettingsSection>
        ) : null}
        {active === "announcements" ? (
          <SettingsSection
            id="settings-announcements"
            title={t("sections.announcements.title")}
            description={t("sections.announcements.description")}
            testId="settings-section-announcements"
          >
            <AnnouncementManager
              initial={announcements}
              loadError={announcementError}
            />
          </SettingsSection>
        ) : null}
        <SettingsForm
          initial={settings}
          activeSection={mainSection}
          onDirty={() => {
            if (mainSection === "hidden") return;
            markDirty(mainSection);
          }}
          onSaved={() => {
            clearDirty(mainSection);
          }}
        />
        <div data-testid="settings-section-download-cache" className={cn(active === "download-cache" ? "" : "hidden")}>
          <DownloadCacheCard initial={settings} onDirty={() => markDirty("download-cache")} onSaved={() => clearDirty("download-cache")} />
        </div>
        <div data-testid="settings-section-ai" className={cn(active === "ai" ? "" : "hidden")}>
          <AiSettingsForm initial={ai} onDirty={() => markDirty("ai")} onSaved={() => clearDirty("ai")} />
        </div>
        <div data-testid="settings-section-transfer" className={cn(active === "transfer" ? "" : "hidden")}>
          <SettingsTransferCard />
        </div>
      </div>
    </div>
  );
}
