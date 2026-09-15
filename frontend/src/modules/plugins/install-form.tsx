"use client";

import { TriangleAlert } from "lucide-react";
import { useTranslations } from "next-intl";
import { InstallFormFields } from "@/modules/plugins/install-form-fields";
import { InstallOperationLog } from "@/modules/plugins/install-operation-log";
import { InstallUninstallPanel } from "@/modules/plugins/install-uninstall-panel";
import { pickDefaultAssetIndex, toggleExclusion } from "@/modules/plugins/market-install-options";
import { PlanSummary } from "@/modules/plugins/plan-summary";
import type { MarketInstallServer } from "@/modules/plugins/types";
import { useInstallForm } from "@/modules/plugins/use-install-form";

export function InstallForm({
  pluginId,
  aiUnreviewed = false,
  pluginTitle,
  githubUrl,
  servers,
  defaultServerId,
  showUninstall = false,
  onQueued,
}: {
  pluginId: number;
  aiUnreviewed?: boolean;
  pluginTitle: string;
  githubUrl: string;
  servers: readonly MarketInstallServer[];
  defaultServerId: number | null;
  showUninstall?: boolean;
  onQueued?: () => void;
}) {
  const t = useTranslations("plugins");
  const form = useInstallForm({
    pluginId,
    aiUnreviewed,
    githubUrl,
    servers,
    defaultServerId,
    onQueued,
  });

  if (servers.length === 0) {
    return <p className="text-sm text-fg-muted">{t("noServers")}</p>;
  }

  return (
    <div className="space-y-4" data-testid="market-install-form">
      {form.error ? (
        <div className="flex items-start gap-2 rounded-md border border-danger/30 bg-danger-muted/50 px-3 py-2 text-sm text-danger">
          <TriangleAlert className="mt-0.5 size-4 shrink-0" />
          <span>{form.error}</span>
        </div>
      ) : null}

      <InstallFormFields
        pluginId={pluginId}
        servers={servers}
        serverId={form.serverId}
        busy={form.busy}
        pending={form.pending}
        loadingVersions={form.loadingVersions}
        releases={form.releases}
        versionsError={form.versionsError}
        selectedServer={form.selectedServer}
        selectedRelease={form.selectedRelease}
        selectedAsset={form.selectedAsset}
        releaseIndex={form.releaseIndex}
        assetIndex={form.assetIndex}
        proxyMode={form.proxyMode}
        upgradeMode={form.upgradeMode}
        installDependencies={form.installDependencies}
        advanced={form.advanced}
        archive={form.archive}
        exclusionMode={form.exclusionMode}
        excludeDirs={form.excludeDirs}
        excludeFiles={form.excludeFiles}
        archiveDirs={form.archiveDirs}
        archiveFiles={form.archiveFiles}
        planReady={form.plan != null}
        planBlocked={Boolean(form.plan?.blocked)}
        onServerId={(serverId) => {
          form.setServerId(serverId);
          form.setPlan(null);
        }}
        onReleaseIndex={(index, release) => {
          form.setReleaseIndex(index);
          form.setAssetIndex(release ? pickDefaultAssetIndex(release.assets) : null);
          form.setArchive(null);
          form.setPlan(null);
        }}
        onAssetIndex={(index) => {
          form.setAssetIndex(index);
          form.setArchive(null);
          form.setPlan(null);
        }}
        onUpgradeMode={(value) => {
          form.setUpgradeMode(value);
          form.setPlan(null);
        }}
        onInstallDependencies={(value) => {
          form.setInstallDependencies(value);
          form.setPlan(null);
        }}
        onAdvanced={form.setAdvanced}
        onExclusionMode={form.setExclusionMode}
        onToggleDir={(dir) => {
          form.setExcludeDirs((current) => toggleExclusion(current, dir));
          form.setPlan(null);
        }}
        onToggleFile={(path) => {
          form.setExcludeFiles((current) => toggleExclusion(current, path));
          form.setPlan(null);
        }}
        onAnalyze={() => void form.analyze()}
        onCheckPlan={() => void form.checkPlan()}
        onInstall={() => void form.install()}
      />

      {form.plan ? <PlanSummary plan={form.plan} /> : null}

      {form.operation ? (
        <InstallOperationLog
          key={form.operation.operationId}
          operation={form.operation}
          pluginTitle={pluginTitle}
          assetName={form.selectedAsset?.name}
          onOperation={form.applyOperation}
        />
      ) : null}

      {showUninstall ? (
        <InstallUninstallPanel
          pluginId={pluginId}
          busy={form.busy || form.serverId == null}
          pending={form.pending}
          onUninstall={form.uninstall}
        />
      ) : null}
    </div>
  );
}
