"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import {
  analyzeGitHubArchiveAction,
  getPluginInstallPlanAction,
  installMarketPluginAction,
  listGitHubReleasesAction,
  listServerMarketPluginIdsAction,
  uninstallMarketPluginAction,
} from "@/modules/plugins/actions";
import {
  installOptionDefaults,
  pickDefaultAssetIndex,
  pluginTrackedOnServer,
  shouldRefreshInstallDefaults,
} from "@/modules/plugins/market-install-options";
import { runtimeMismatchValues } from "@/modules/plugins/runtime-labels";
import { useRuntimeLabel } from "@/modules/plugins/plan-summary";
import type {
  GitHubArchive,
  GitHubRelease,
  MarketInstallServer,
  PluginInstallPlan,
} from "@/modules/plugins/types";
import { trackQueuedOperation } from "@/modules/servers/activity-store";
import { confirm, notify } from "@/shared/feedback";
import { isActiveOperation, serverProxyMode, type ServerOperation } from "@/modules/servers/types";

export function useInstallForm({
  pluginId,
  aiUnreviewed,
  githubUrl,
  servers,
  defaultServerId,
  onQueued,
}: {
  pluginId: number;
  aiUnreviewed: boolean;
  githubUrl: string;
  servers: readonly MarketInstallServer[];
  defaultServerId: number | null;
  onQueued?: () => void;
}) {
  const t = useTranslations("plugins");
  const runtimeLabel = useRuntimeLabel();
  const router = useRouter();
  const [serverId, setServerId] = useState<number | null>(
    defaultServerId ?? servers[0]?.id ?? null,
  );
  const [releaseQuery, setReleaseQuery] = useState<{
    readonly key: string;
    readonly releases: readonly GitHubRelease[];
    readonly error: string | null;
  }>({ key: "", releases: [], error: null });
  const [releaseIndex, setReleaseIndex] = useState<number | null>(null);
  const [assetIndex, setAssetIndex] = useState<number | null>(null);
  const [upgradeMode, setUpgradeMode] = useState(
    () => installOptionDefaults(false).upgradeMode,
  );
  const [installDependencies, setInstallDependencies] = useState(
    () => installOptionDefaults(false).installDependencies,
  );
  const presenceByServer = useRef(new Map<number, boolean>());
  const [advanced, setAdvanced] = useState(false);
  const [archive, setArchive] = useState<GitHubArchive | null>(null);
  const [exclusionMode, setExclusionMode] = useState<"directory" | "file">("directory");
  const [excludeDirs, setExcludeDirs] = useState<string[]>([]);
  const [excludeFiles, setExcludeFiles] = useState<string[]>([]);
  const [plan, setPlan] = useState<PluginInstallPlan | null>(null);
  const [operation, setOperation] = useState<ServerOperation | null>(null);
  const applyOperation = useCallback((update: (current: ServerOperation) => ServerOperation) => {
    setOperation((current) => (current ? update(current) : current));
  }, []);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const releaseKey = `${serverId ?? ""}:${githubUrl}`;
  const loadingVersions =
    serverId != null && Boolean(githubUrl) && releaseQuery.key !== releaseKey;
  const releases = loadingVersions ? [] : releaseQuery.releases;
  const versionsError = loadingVersions ? null : releaseQuery.error;
  const selectedServer = servers.find((server) => server.id === serverId);
  const selectedRelease = releaseIndex != null ? (releases[releaseIndex] ?? null) : null;
  const selectedAsset =
    selectedRelease && assetIndex != null ? (selectedRelease.assets[assetIndex] ?? null) : null;
  const busy = pending || isActiveOperation(operation);
  const proxyMode = serverProxyMode({
    usePanelProxy: selectedServer?.usePanelProxy ?? false,
    githubProxy: selectedServer?.githubProxy ?? null,
  });
  const archiveDirs = useMemo(() => archive?.allDirs ?? [], [archive]);
  const archiveFiles = useMemo(
    () => archive?.allFiles.filter((item) => !item.isDir) ?? [],
    [archive],
  );

  useEffect(() => {
    presenceByServer.current.clear();
  }, [pluginId]);

  useEffect(() => {
    if (serverId == null) return;
    const apply = (existsOnServer: boolean) => {
      const defaults = installOptionDefaults(existsOnServer);
      setUpgradeMode(defaults.upgradeMode);
      setInstallDependencies(defaults.installDependencies);
      setPlan(null);
    };
    const cached = presenceByServer.current.get(serverId);
    if (cached != null) {
      apply(cached);
      return;
    }
    apply(false);
    let cancelled = false;
    void listServerMarketPluginIdsAction(serverId).then((result) => {
      if (cancelled) return;
      const exists = result.ok && pluginTrackedOnServer(result.data, pluginId);
      presenceByServer.current.set(serverId, exists);
      if (shouldRefreshInstallDefaults(false, exists)) apply(exists);
    });
    return () => {
      cancelled = true;
    };
  }, [pluginId, serverId]);

  useEffect(() => {
    if (serverId == null || !githubUrl) return;
    const key = `${serverId}:${githubUrl}`;
    let cancelled = false;
    void listGitHubReleasesAction(githubUrl, serverId).then((result) => {
      if (cancelled) return;
      if (!result.ok) {
        setReleaseQuery({ key, releases: [], error: result.error });
        setReleaseIndex(null);
        setAssetIndex(null);
        setArchive(null);
        return;
      }
      if (result.data.releases.length === 0) {
        setReleaseQuery({ key, releases: [], error: t("noVersions") });
        setReleaseIndex(null);
        setAssetIndex(null);
        setArchive(null);
        return;
      }
      setReleaseQuery({
        key,
        releases: result.data.releases,
        error: null,
      });
      setReleaseIndex(0);
      setAssetIndex(pickDefaultAssetIndex(result.data.releases[0]?.assets ?? []));
      setArchive(null);
    });
    return () => {
      cancelled = true;
    };
  }, [githubUrl, serverId, t]);

  async function checkPlan() {
    if (serverId == null) {
      setError(t("needServer"));
      return;
    }
    if (!selectedAsset) {
      setError(t("needVersion"));
      return;
    }
    setPending(true);
    setError(null);
    const result = await getPluginInstallPlanAction(serverId, pluginId, installDependencies);
    setPending(false);
    if (!result.ok) {
      setPlan(null);
      setError(result.error);
      return;
    }
    setPlan(result.data);
  }

  async function analyze() {
    if (serverId == null || !selectedAsset) return;
    setPending(true);
    setError(null);
    const result = await analyzeGitHubArchiveAction(serverId, selectedAsset.browserDownloadUrl);
    setPending(false);
    if (!result.ok) {
      setError(result.error);
      return;
    }
    setArchive(result.data);
    setExcludeDirs([]);
    setExcludeFiles([]);
    setPlan(null);
  }

  async function install() {
    if (serverId == null || !plan) return;
    if (plan.blocked) return;
    if (
      (aiUnreviewed || (plan.aiUnreviewed?.length ?? 0) > 0) &&
      !(await confirm({ title: t("aiImport.needsReview"), description: t("aiImport.warning") }))
    ) {
      return;
    }
    if (!selectedAsset) {
      setError(t("needVersion"));
      return;
    }
    if (plan.framework.mismatch) {
      if (
        !(await confirm({
          title: t("frameworkMismatchTitle"),
          description: `${t(
            "frameworkMismatch",
            runtimeMismatchValues(plan.framework, runtimeLabel),
          )}\n${t("frameworkMismatchConfirm")}`,
          confirmLabel: t("install"),
          tone: "danger",
        }))
      ) {
        return;
      }
    }
    if (plan.warnings.length > 0) {
      const details = plan.warnings.map((item) => `#${item.ruleId}: ${item.reason}`).join("\n");
      if (!(await confirm({ title: t("confirmWarnings"), description: details }))) {
        return;
      }
    }
    setPending(true);
    setError(null);
    const result = await installMarketPluginAction(serverId, pluginId, {
      acknowledgeWarningRuleIds: plan.warnings.map((item) => item.ruleId),
      acknowledgeFrameworkMismatch: plan.framework.mismatch,
      acknowledgeAIUnreviewed: aiUnreviewed || (plan.aiUnreviewed?.length ?? 0) > 0,
      planHash: plan.planHash,
      downloadUrl: selectedAsset.browserDownloadUrl,
      upgradeMode,
      installDependencies,
      excludeDirs,
      excludeFiles,
    });
    setPending(false);
    if (!result.ok) {
      setError(result.error);
      return;
    }
    setOperation(result.data);
    trackQueuedOperation(result.data, {
      serverName: servers.find((server) => server.id === serverId)?.name,
      latestMessage: result.data.message,
    });
    notify.info(t("queuedToTray"));
    onQueued?.();
  }

  async function uninstall(files: string[]) {
    if (serverId == null) return;
    if (files.length === 0) return;
    if (!(await confirm(t("github.uninstallConfirm", { count: files.length })))) {
      return null;
    }
    setPending(true);
    setError(null);
    const result = await uninstallMarketPluginAction(serverId, pluginId, files);
    setPending(false);
    if (!result.ok) {
      setError(result.error);
      return null;
    }
    setOperation(result.data);
    router.refresh();
    return result.data.message || t("github.uninstallQueued");
  }

  return {
    serverId,
    setServerId,
    setPlan,
    releaseIndex,
    setReleaseIndex,
    assetIndex,
    setAssetIndex,
    setArchive,
    upgradeMode,
    setUpgradeMode,
    installDependencies,
    setInstallDependencies,
    advanced,
    setAdvanced,
    archive,
    exclusionMode,
    setExclusionMode,
    excludeDirs,
    setExcludeDirs,
    excludeFiles,
    setExcludeFiles,
    plan,
    operation,
    applyOperation,
    pending,
    error,
    setError,
    loadingVersions,
    releases,
    versionsError,
    selectedServer,
    selectedRelease,
    selectedAsset,
    busy,
    proxyMode,
    archiveDirs,
    archiveFiles,
    checkPlan,
    analyze,
    install,
    uninstall,
  };
}
