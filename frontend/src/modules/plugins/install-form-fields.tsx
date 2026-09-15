"use client";

import { memo } from "react";
import { useTranslations } from "next-intl";
import { Download } from "lucide-react";
import { GitHubRequestError } from "@/modules/plugins/github-request-error";
import { formatArchiveSize } from "@/modules/plugins/market-install-options";
import type {
  GitHubArchive,
  GitHubRelease,
  GitHubReleaseAsset,
  MarketInstallServer,
} from "@/modules/plugins/types";
import type { ServerProxyMode } from "@/modules/servers/types";
import { Badge } from "@/shared/ui/badge";
import { Button } from "@/shared/ui/button";
import { Label } from "@/shared/ui/input";
import { Select } from "@/shared/ui/select";

type InstallFormFieldsProps = {
  pluginId: number;
  servers: readonly MarketInstallServer[];
  serverId: number | null;
  busy: boolean;
  pending: boolean;
  loadingVersions: boolean;
  releases: readonly GitHubRelease[];
  versionsError: string | null;
  selectedServer: MarketInstallServer | undefined;
  selectedRelease: GitHubRelease | null;
  selectedAsset: GitHubReleaseAsset | null;
  releaseIndex: number | null;
  assetIndex: number | null;
  proxyMode: ServerProxyMode;
  upgradeMode: boolean;
  installDependencies: boolean;
  advanced: boolean;
  archive: GitHubArchive | null;
  exclusionMode: "directory" | "file";
  excludeDirs: readonly string[];
  excludeFiles: readonly string[];
  archiveDirs: readonly string[];
  archiveFiles: readonly { readonly path: string }[];
  planReady: boolean;
  planBlocked: boolean;
  onServerId: (serverId: number) => void;
  onReleaseIndex: (index: number | null, release: GitHubRelease | null) => void;
  onAssetIndex: (index: number) => void;
  onUpgradeMode: (value: boolean) => void;
  onInstallDependencies: (value: boolean) => void;
  onAdvanced: (value: boolean) => void;
  onExclusionMode: (mode: "directory" | "file") => void;
  onToggleDir: (dir: string) => void;
  onToggleFile: (path: string) => void;
  onAnalyze: () => void;
  onCheckPlan: () => void;
  onInstall: () => void;
};

export const InstallFormFields = memo(function InstallFormFields({
  pluginId,
  servers,
  serverId,
  busy,
  pending,
  loadingVersions,
  releases,
  versionsError,
  selectedServer,
  selectedRelease,
  selectedAsset,
  releaseIndex,
  assetIndex,
  proxyMode,
  upgradeMode,
  installDependencies,
  advanced,
  archive,
  exclusionMode,
  excludeDirs,
  excludeFiles,
  archiveDirs,
  archiveFiles,
  planReady,
  planBlocked,
  onServerId,
  onReleaseIndex,
  onAssetIndex,
  onUpgradeMode,
  onInstallDependencies,
  onAdvanced,
  onExclusionMode,
  onToggleDir,
  onToggleFile,
  onAnalyze,
  onCheckPlan,
  onInstall,
}: InstallFormFieldsProps) {
  const t = useTranslations("plugins");
  return (
    <>
      <div>
        <Label htmlFor={`install-server-${pluginId}`}>{t("targetServer")}</Label>
        <Select
          id={`install-server-${pluginId}`}
          value={serverId ?? ""}
          disabled={busy}
          onChange={(event) => onServerId(Number(event.target.value))}
        >
          {servers.map((server) => (
            <option key={server.id} value={server.id}>
              {server.name}
            </option>
          ))}
        </Select>
      </div>

      <div
        className="rounded-md border border-line bg-surface-overlay/40 px-3 py-2 text-xs text-fg-muted"
        data-testid="market-download-mode"
      >
        <span className="font-medium text-fg">{t("github.downloadMode")}: </span>
        {proxyMode === "panel"
          ? t("github.usingPanelProxy")
          : proxyMode === "github_url"
            ? `${t("github.usingGithubProxy")} ${selectedServer?.githubProxy}`
            : t("github.usingDirect")}
      </div>

      <div>
        <Label htmlFor={`install-version-${pluginId}`}>{t("github.selectRelease")}</Label>
        <Select
          id={`install-version-${pluginId}`}
          value={releaseIndex ?? ""}
          disabled={busy || loadingVersions || releases.length === 0}
          onChange={(event) => {
            const next = event.target.value === "" ? null : Number(event.target.value);
            const release = next != null ? (releases[next] ?? null) : null;
            onReleaseIndex(next, release);
          }}
        >
          <option value="">
            {loadingVersions ? t("loadingVersions") : t("selectVersionPlaceholder")}
          </option>
          {releases.map((release, index) => (
            <option key={`${release.tagName}-${index}`} value={index}>
              {release.name || release.tagName}
              {index === 0 ? ` (${t("latest")})` : ""}
              {release.prerelease ? ` [${t("prerelease")}]` : ""}
            </option>
          ))}
        </Select>
        {versionsError ? <GitHubRequestError error={versionsError} /> : null}
      </div>

      {selectedRelease ? (
        <fieldset className="space-y-2">
          <legend className="text-sm font-medium text-fg-muted">{t("github.selectAsset")}</legend>
          <div className="space-y-1.5">
            {selectedRelease.assets.map((asset, index) => (
              <label
                key={`${asset.name}-${index}`}
                className="flex cursor-pointer items-center justify-between gap-3 rounded-md border border-line bg-surface-overlay/40 px-3 py-2 text-sm"
              >
                <span className="flex min-w-0 items-center gap-2">
                  <input
                    type="radio"
                    name={`market-asset-${pluginId}`}
                    className="size-4 accent-primary"
                    checked={assetIndex === index}
                    disabled={busy}
                    onChange={() => onAssetIndex(index)}
                  />
                  <span className="min-w-0 truncate font-mono text-xs">{asset.name}</span>
                  {asset.runtimeCompatibility === "recommended" ? (
                    <Badge tone="primary">{t("assetRecommended")}</Badge>
                  ) : null}
                  {asset.runtimeCompatibility === "alternative" ? (
                    <Badge tone="warn">{t("assetAlternative")}</Badge>
                  ) : null}
                  {asset.runtimeCompatibility === "unknown" ? (
                    <Badge tone="neutral">{t("assetUnknown")}</Badge>
                  ) : null}
                </span>
                <span className="shrink-0 text-xs text-fg-subtle">{formatArchiveSize(asset.size)}</span>
              </label>
            ))}
          </div>
        </fieldset>
      ) : null}

      <label className="flex items-start gap-2 text-sm text-fg-muted">
        <input
          type="checkbox"
          className="mt-0.5 size-4 rounded border-line accent-primary"
          data-testid="market-upgrade-mode"
          checked={upgradeMode}
          disabled={busy}
          onChange={(event) => onUpgradeMode(event.target.checked)}
        />
        <span>
          <span className="font-medium text-fg">{t("upgradeMode")}</span>
          <span className="mt-0.5 block text-xs text-fg-subtle">{t("upgradeModeHelp")}</span>
        </span>
      </label>

      <label className="flex items-start gap-2 text-sm text-fg-muted">
        <input
          type="checkbox"
          className="mt-0.5 size-4 rounded border-line accent-primary"
          data-testid="market-install-dependencies"
          checked={installDependencies}
          disabled={busy}
          onChange={(event) => onInstallDependencies(event.target.checked)}
        />
        <span>
          <span className="font-medium text-fg">{t("installDependenciesOptIn")}</span>
          <span className="mt-0.5 block text-xs text-fg-subtle">{t("installDependenciesHelp")}</span>
        </span>
      </label>

      <label className="flex items-start gap-2 text-sm text-fg-muted">
        <input
          type="checkbox"
          className="mt-0.5 size-4 rounded border-line accent-primary"
          checked={advanced}
          disabled={busy}
          onChange={(event) => onAdvanced(event.target.checked)}
        />
        <span className="font-medium text-fg">{t("advancedOptions")}</span>
      </label>

      {advanced ? (
        <div
          className="space-y-3 rounded-md border border-line bg-surface-overlay/40 px-4 py-3"
          data-testid="market-exclude-toggles"
        >
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div>
              <p className="text-sm font-medium text-fg">{t("github.excludeTitle")}</p>
              <p className="mt-1 text-xs text-fg-subtle">{t("github.excludeHint")}</p>
            </div>
            <Button
              type="button"
              variant="secondary"
              size="sm"
              disabled={busy || !selectedAsset}
              onClick={onAnalyze}
            >
              {pending && !archive ? t("github.analyzing") : t("github.analyze")}
            </Button>
          </div>
          <div className="flex gap-2">
            <Button
              type="button"
              size="sm"
              variant={exclusionMode === "directory" ? "secondary" : "ghost"}
              onClick={() => onExclusionMode("directory")}
            >
              {t("github.byDirectory")}
            </Button>
            <Button
              type="button"
              size="sm"
              variant={exclusionMode === "file" ? "secondary" : "ghost"}
              onClick={() => onExclusionMode("file")}
            >
              {t("github.byFile")}
            </Button>
          </div>
          {archive && exclusionMode === "directory" ? (
            <div className="max-h-40 space-y-1 overflow-auto text-sm">
              {archiveDirs.length === 0 ? (
                <p className="text-xs text-fg-subtle">{t("github.noItems")}</p>
              ) : (
                archiveDirs.map((dir) => (
                  <label key={dir} className="flex items-center gap-2">
                    <input
                      type="checkbox"
                      className="size-4 accent-primary"
                      checked={excludeDirs.includes(dir)}
                      onChange={() => onToggleDir(dir)}
                    />
                    <span className="font-mono text-xs">{dir}</span>
                  </label>
                ))
              )}
            </div>
          ) : null}
          {archive && exclusionMode === "file" ? (
            <div className="max-h-40 space-y-1 overflow-auto text-sm">
              {archiveFiles.length === 0 ? (
                <p className="text-xs text-fg-subtle">{t("github.noItems")}</p>
              ) : (
                archiveFiles.map((file) => (
                  <label key={file.path} className="flex items-center gap-2">
                    <input
                      type="checkbox"
                      className="size-4 accent-primary"
                      checked={excludeFiles.includes(file.path)}
                      onChange={() => onToggleFile(file.path)}
                    />
                    <span className="font-mono text-xs">{file.path}</span>
                  </label>
                ))
              )}
            </div>
          ) : null}
          {!archive ? <p className="text-xs text-fg-subtle">{t("github.noItems")}</p> : null}
        </div>
      ) : null}

      <div className="flex flex-wrap gap-2">
        <Button
          type="button"
          variant="secondary"
          disabled={busy || serverId == null || !selectedAsset}
          onClick={onCheckPlan}
        >
          {pending && !planReady ? t("checking") : t("checkPlan")}
        </Button>
        <Button
          type="button"
          disabled={busy || !planReady || planBlocked || !selectedAsset}
          onClick={onInstall}
        >
          <Download className="size-4" />
          {pending && planReady ? t("installing") : t("install")}
        </Button>
      </div>
    </>
  );
});
