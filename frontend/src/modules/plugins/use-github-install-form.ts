"use client";

import {
analyzeGitHubArchiveAction,
installGitHubPluginAction,
listGitHubReleasesAction,
planGitHubPluginInstallAction,
uninstallGitHubPluginAction,
} from "@/modules/plugins/actions";
import type {
GitHubArchive,
GitHubInstallPlan,
GitHubRelease,
} from "@/modules/plugins/types";
import { trackQueuedOperation } from "@/modules/servers/activity-store";
import {
mergeOperationEvents,
operationEventsUrl,
parseOperationEvent,
} from "@/modules/servers/operation-events";
import type {
OperationStreamEvent,
ServerOperation,
} from "@/modules/servers/types";
import { serverProxyMode } from "@/modules/servers/types";
import { confirm,notify } from "@/shared/feedback";
import {
createRenderCoalescer,
isTerminalOperationEventType,
} from "@/shared/lib/render-coalesce";
import { useTranslations } from "next-intl";
import { useEffect,useMemo,useState } from "react";

import { type ServerOption } from "./github-install-form-parts";

export function useGitHubInstallForm({
  servers,
  defaultServerId,
  variant = "card",
}: {
  servers: readonly ServerOption[];
  defaultServerId: number | null;
  variant?: "card" | "plain";
}) {
  const t = useTranslations("plugins");
  const [serverId, setServerId] = useState<number | null>(
    defaultServerId ?? servers[0]?.id ?? null,
  );
  const [repoUrl, setRepoUrl] = useState("");
  const [releases, setReleases] = useState<readonly GitHubRelease[]>([]);
  const [releaseIndex, setReleaseIndex] = useState<number | null>(null);
  const [assetIndex, setAssetIndex] = useState<number | null>(null);
  const [archive, setArchive] = useState<GitHubArchive | null>(null);
  const [plan, setPlan] = useState<GitHubInstallPlan | null>(null);
  const [operation, setOperation] = useState<ServerOperation | null>(null);
  const [events, setEvents] = useState<OperationStreamEvent[]>([]);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [exclusionMode, setExclusionMode] = useState<"directory" | "file">(
    "directory",
  );
  const [excludeDirs, setExcludeDirs] = useState<string[]>([]);
  const [excludeFiles, setExcludeFiles] = useState<string[]>([]);
  const [sourcePrefix, setSourcePrefix] = useState("");
  const [targetPrefix, setTargetPrefix] = useState<string>("addons");
  const [customTarget, setCustomTarget] = useState("");
  const [useCustomMapping, setUseCustomMapping] = useState(false);
  const [deleteFiles, setDeleteFiles] = useState<string[]>([]);

  const selectedServer = servers.find((server) => server.id === serverId);
  const selectedRelease =
    releaseIndex != null ? (releases[releaseIndex] ?? null) : null;
  const selectedAsset =
    selectedRelease && assetIndex != null
      ? (selectedRelease.assets[assetIndex] ?? null)
      : null;
  const resolvedTarget =
    targetPrefix === "custom" ? customTarget.trim() : targetPrefix;
  const mappingSource = sourcePrefix.trim() || null;
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
    if (!operation) return;
    const source = new EventSource(
      operationEventsUrl(operation.serverId, operation.operationId),
    );
    const coalescer = createRenderCoalescer<OperationStreamEvent>((batch) => {
      setEvents((current) => mergeOperationEvents(current, batch));
    });
    const ingest = (raw: string) => {
      const event = parseOperationEvent(raw);
      if (!event) return;
      coalescer.push(event, {
        immediate: isTerminalOperationEventType(event.type),
      });
    };
    source.onmessage = (message) => ingest(message.data);
    source.addEventListener("progress", (message: MessageEvent<string>) =>
      ingest(message.data),
    );
    // Close on the terminal event so the browser does not reconnect and replay
    // the finished log every few seconds.
    const finish = (message: MessageEvent<string>) => {
      source.close();
      ingest(message.data);
    };
    source.addEventListener("operation_completed", finish);
    source.addEventListener("operation_failed", finish);
    return () => {
      coalescer.dispose();
      source.close();
    };
  }, [operation]);

  const mappingEnabled = useCustomMapping || Boolean(plan?.mappingRequired);

  function mappingPayload() {
    if (!mappingEnabled) {
      return {
    excludeDirs,
    excludeFiles,
  };
    }
    return {
      sourcePrefix: mappingSource,
      targetPrefix: resolvedTarget || null,
      excludeDirs,
      excludeFiles,
    };
  }

  async function fetchReleases() {
    if (!repoUrl.trim()) return;
    setPending(true);
    setError(null);
    setReleases([]);
    setReleaseIndex(null);
    setAssetIndex(null);
    setArchive(null);
    setPlan(null);
    const result = await listGitHubReleasesAction(
      repoUrl.trim(),
      serverId ?? undefined,
    );
    setPending(false);
    if (!result.ok) {
      setError(result.error);
      return;
    }
    setReleases(result.data.releases);
    if (result.data.releases.length === 0) {
      setError(t("github.noReleases"));
    }
  }

  async function analyze() {
    if (serverId == null || !selectedAsset) return;
    setPending(true);
    setError(null);
    const result = await analyzeGitHubArchiveAction(
      serverId,
      selectedAsset.browserDownloadUrl,
    );
    setPending(false);
    if (!result.ok) {
      setError(result.error);
      return;
    }
    setArchive(result.data);
    setExcludeDirs([]);
    setExcludeFiles([]);
    setDeleteFiles([]);
    if (result.data.rootDirs[0] && !sourcePrefix) {
      setSourcePrefix(result.data.rootDirs[0]);
    }
  }

  async function checkPlan() {
    if (serverId == null || !selectedAsset) return;
    setPending(true);
    setError(null);
    const result = await planGitHubPluginInstallAction(serverId, {
      repoUrl: repoUrl.trim(),
      assetName: selectedAsset.name,
      ...mappingPayload(),
    });
    setPending(false);
    if (!result.ok) {
      setPlan(null);
      setError(result.error);
      return;
    }
    setPlan(result.data);
    if (result.data.mappingRequired) setUseCustomMapping(true);
  }

  async function install() {
    if (serverId == null || !selectedAsset || !plan) return;
    if (plan.mappingRequired || plan.hardConflicts.length > 0) return;
    if (plan.conflictWarnings.length > 0 || plan.warnings.length > 0) {
      const details = [
        ...plan.warnings,
        ...plan.conflictWarnings.map(
          (item) => `#${item.ruleId}: ${item.reason}`,
        ),
      ].join("\n");
      if (
        !(await confirm({
          title: t("confirmWarnings"),
          description: details,
        }))
      ) {
        return;
      }
    }
    setPending(true);
    setError(null);
    const result = await installGitHubPluginAction(serverId, {
      repoUrl: repoUrl.trim(),
      assetName: selectedAsset.name,
      expectedPlanHash: plan.planHash,
      acknowledgeWarningRuleIds: plan.conflictWarnings.map(
        (item) => item.ruleId,
      ),
      acknowledgeUnknownCompatibility: plan.compatibilityUnknown,
      ...mappingPayload(),
    });
    setPending(false);
    if (!result.ok) {
      setError(result.error);
      return;
    }
    setOperation(result.data);
    setEvents([]);
    trackQueuedOperation(result.data, {
      serverName: selectedServer?.name,
      latestMessage: result.data.message,
    });
    notify.info(t("queuedToTray"));
  }

  async function uninstall() {
    if (serverId == null || deleteFiles.length === 0) return;
    if (!(await confirm(t("github.uninstallConfirm", { count: deleteFiles.length })))) {
      return;
    }
    setPending(true);
    setError(null);
    const result = await uninstallGitHubPluginAction(serverId, {
      filesToDelete: deleteFiles,
    });
    setPending(false);
    if (!result.ok) {
      setError(result.error);
      return;
    }
    setOperation(result.data);
    setEvents([]);
  }

  
  return {
    t,
    serverId,
    setServerId,
    repoUrl,
    setRepoUrl,
    releases,
    releaseIndex,
    setReleaseIndex,
    assetIndex,
    setAssetIndex,
    archive,
    setArchive,
    plan,
    setPlan,
    operation,
    events,
    pending,
    error,
    exclusionMode,
    setExclusionMode,
    excludeDirs,
    setExcludeDirs,
    excludeFiles,
    setExcludeFiles,
    sourcePrefix,
    setSourcePrefix,
    targetPrefix,
    setTargetPrefix,
    customTarget,
    setCustomTarget,
    setUseCustomMapping,
    deleteFiles,
    setDeleteFiles,
    selectedServer,
    selectedRelease,
    selectedAsset,
    proxyMode,
    archiveDirs,
    archiveFiles,
    mappingEnabled,
    fetchReleases,
    analyze,
    checkPlan,
    install,
    uninstall,
  };
}
