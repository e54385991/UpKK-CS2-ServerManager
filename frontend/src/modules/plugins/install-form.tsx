"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { Download, Trash2, TriangleAlert } from "lucide-react";
import {
  analyzeGitHubArchiveAction,
  getPluginInstallPlanAction,
  installMarketPluginAction,
  listGitHubReleasesAction,
  listServerMarketPluginIdsAction,
  uninstallMarketPluginAction,
} from "@/modules/plugins/actions";
import {
  formatArchiveSize,
  installOptionDefaults,
  pickDefaultAssetIndex,
  pluginTrackedOnServer,
  toggleExclusion,
} from "@/modules/plugins/market-install-options";
import type {
  GitHubArchive,
  GitHubRelease,
  MarketInstallServer,
  PluginInstallPlan,
} from "@/modules/plugins/types";
import { PlanSummary, useRuntimeLabel } from "@/modules/plugins/plan-summary";
import { runtimeMismatchValues } from "@/modules/plugins/runtime-labels";
import { trackQueuedOperation } from "@/modules/servers/activity-store";
import { confirm, notify } from "@/shared/feedback";
import {
  mergeOperationEvents,
  operationEventsUrl,
  parseOperationEvent,
} from "@/modules/servers/operation-events";
import {
  isActiveOperation,
  serverProxyMode,
  type OperationStreamEvent,
  type ServerOperation,
} from "@/modules/servers/types";
import { Badge } from "@/shared/ui/badge";
import { Button } from "@/shared/ui/button";
import { Label } from "@/shared/ui/input";
import { Select } from "@/shared/ui/select";
import { Textarea } from "@/shared/ui/textarea";

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
  const [exclusionMode, setExclusionMode] = useState<"directory" | "file">(
    "directory",
  );
  const [excludeDirs, setExcludeDirs] = useState<string[]>([]);
  const [excludeFiles, setExcludeFiles] = useState<string[]>([]);
  const [plan, setPlan] = useState<PluginInstallPlan | null>(null);
  const [operation, setOperation] = useState<ServerOperation | null>(null);
  const [events, setEvents] = useState<OperationStreamEvent[]>([]);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [deleteText, setDeleteText] = useState("");
  const [uninstallNotice, setUninstallNotice] = useState<string | null>(null);

  const releaseKey = `${serverId ?? ""}:${githubUrl}`;
  const loadingVersions =
    serverId != null && Boolean(githubUrl) && releaseQuery.key !== releaseKey;
  const releases = loadingVersions ? [] : releaseQuery.releases;
  const versionsError = loadingVersions ? null : releaseQuery.error;
  const selectedServer = servers.find((server) => server.id === serverId);
  const selectedRelease =
    releaseIndex != null ? (releases[releaseIndex] ?? null) : null;
  const selectedAsset =
    selectedRelease && assetIndex != null
      ? (selectedRelease.assets[assetIndex] ?? null)
      : null;
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
      const exists =
        result.ok && pluginTrackedOnServer(result.data, pluginId);
      presenceByServer.current.set(serverId, exists);
      apply(exists);
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

  useEffect(() => {
    if (!operation) return;
    const source = new EventSource(
      operationEventsUrl(operation.serverId, operation.operationId),
    );
    const ingest = (raw: string) => {
      const event = parseOperationEvent(raw);
      if (!event) return null;
      setEvents((current) => mergeOperationEvents(current, [event]));
      return event;
    };
    source.onmessage = (message) => {
      ingest(message.data);
    };
    source.addEventListener("progress", (message: MessageEvent<string>) => {
      ingest(message.data);
    });
    source.addEventListener(
      "operation_completed",
      (message: MessageEvent<string>) => {
        const event = ingest(message.data);
        setOperation((current) =>
          current
            ? {
                ...current,
                status: "completed",
                success: true,
                message: event?.message ?? current.message,
              }
            : current,
        );
        router.refresh();
      },
    );
    source.addEventListener(
      "operation_failed",
      (message: MessageEvent<string>) => {
        const event = ingest(message.data);
        setOperation((current) =>
          current
            ? {
                ...current,
                status: "failed",
                success: false,
                message: event?.message ?? current.message,
              }
            : current,
        );
      },
    );
    return () => source.close();
  }, [operation, router]);

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
    const result = await getPluginInstallPlanAction(
      serverId,
      pluginId,
      installDependencies,
    );
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
    setPlan(null);
  }

  async function install() {
    if (serverId == null || !plan) return;
    if (plan.blocked) return;
    if ((aiUnreviewed || (plan.aiUnreviewed?.length ?? 0) > 0) && !(await confirm({ title: t("aiImport.needsReview"), description: t("aiImport.warning") }))) return;
    if (!selectedAsset) {
      setError(t("needVersion"));
      return;
    }
    // Foolproofing: a CounterStrikeSharp plugin never loads on a SwiftlyS2
    // server (and the reverse), so the operator has to confirm explicitly and
    // the backend refuses the install without the acknowledgement.
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
      const details = plan.warnings
        .map((item) => `#${item.ruleId}: ${item.reason}`)
        .join("\n");
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
    setEvents([]);
    trackQueuedOperation(result.data, {
      serverName: servers.find((server) => server.id === serverId)?.name,
      latestMessage: result.data.message,
    });
    notify.info(t("queuedToTray"));
    onQueued?.();
  }

  async function uninstall() {
    if (serverId == null) return;
    const files = deleteText
      .split(/[\n,]/)
      .map((item) => item.trim())
      .filter(Boolean);
    if (files.length === 0) return;
    if (!(await confirm(t("github.uninstallConfirm", { count: files.length })))) {
      return;
    }
    setPending(true);
    setError(null);
    setUninstallNotice(null);
    const result = await uninstallMarketPluginAction(serverId, pluginId, files);
    setPending(false);
    if (!result.ok) {
      setError(result.error);
      return;
    }
    setUninstallNotice(result.data.message || t("github.uninstallQueued"));
    setOperation(result.data);
    setEvents([]);
    router.refresh();
  }

  if (servers.length === 0) {
    return <p className="text-sm text-fg-muted">{t("noServers")}</p>;
  }

  return (
    <div className="space-y-4" data-testid="market-install-form">
      {error ? (
        <div className="flex items-start gap-2 rounded-md border border-danger/30 bg-danger-muted/50 px-3 py-2 text-sm text-danger">
          <TriangleAlert className="mt-0.5 size-4 shrink-0" />
          <span>{error}</span>
        </div>
      ) : null}

      <div>
        <Label htmlFor={`install-server-${pluginId}`}>{t("targetServer")}</Label>
        <Select
          id={`install-server-${pluginId}`}
          value={serverId ?? ""}
          disabled={busy}
          onChange={(event) => {
            setServerId(Number(event.target.value));
            setPlan(null);
          }}
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
        <Label htmlFor={`install-version-${pluginId}`}>
          {t("github.selectRelease")}
        </Label>
        <Select
          id={`install-version-${pluginId}`}
          value={releaseIndex ?? ""}
          disabled={busy || loadingVersions || releases.length === 0}
          onChange={(event) => {
            const next =
              event.target.value === "" ? null : Number(event.target.value);
            setReleaseIndex(next);
            const release = next != null ? releases[next] : null;
            setAssetIndex(
              release ? pickDefaultAssetIndex(release.assets) : null,
            );
            setArchive(null);
            setPlan(null);
          }}
        >
          <option value="">
            {loadingVersions
              ? t("loadingVersions")
              : t("selectVersionPlaceholder")}
          </option>
          {releases.map((release, index) => (
            <option key={`${release.tagName}-${index}`} value={index}>
              {release.name || release.tagName}
              {index === 0 ? ` (${t("latest")})` : ""}
              {release.prerelease ? ` [${t("prerelease")}]` : ""}
            </option>
          ))}
        </Select>
        {versionsError ? (
          <p className="mt-1 text-xs text-danger">{versionsError}</p>
        ) : null}
      </div>

      {selectedRelease ? (
        <fieldset className="space-y-2">
          <legend className="text-sm font-medium text-fg-muted">
            {t("github.selectAsset")}
          </legend>
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
                    onChange={() => {
                      setAssetIndex(index);
                      setArchive(null);
                      setPlan(null);
                    }}
                  />
                  <span className="min-w-0 truncate font-mono text-xs">
                    {asset.name}
                  </span>
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
                <span className="shrink-0 text-xs text-fg-subtle">
                  {formatArchiveSize(asset.size)}
                </span>
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
          onChange={(event) => {
            setUpgradeMode(event.target.checked);
            setPlan(null);
          }}
        />
        <span>
          <span className="font-medium text-fg">{t("upgradeMode")}</span>
          <span className="mt-0.5 block text-xs text-fg-subtle">
            {t("upgradeModeHelp")}
          </span>
        </span>
      </label>

      <label className="flex items-start gap-2 text-sm text-fg-muted">
        <input
          type="checkbox"
          className="mt-0.5 size-4 rounded border-line accent-primary"
          data-testid="market-install-dependencies"
          checked={installDependencies}
          disabled={busy}
          onChange={(event) => {
            setInstallDependencies(event.target.checked);
            setPlan(null);
          }}
        />
        <span>
          <span className="font-medium text-fg">
            {t("installDependenciesOptIn")}
          </span>
          <span className="mt-0.5 block text-xs text-fg-subtle">
            {t("installDependenciesHelp")}
          </span>
        </span>
      </label>

      <label className="flex items-start gap-2 text-sm text-fg-muted">
        <input
          type="checkbox"
          className="mt-0.5 size-4 rounded border-line accent-primary"
          checked={advanced}
          disabled={busy}
          onChange={(event) => setAdvanced(event.target.checked)}
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
              <p className="text-sm font-medium text-fg">
                {t("github.excludeTitle")}
              </p>
              <p className="mt-1 text-xs text-fg-subtle">
                {t("github.excludeHint")}
              </p>
            </div>
            <Button
              type="button"
              variant="secondary"
              size="sm"
              disabled={busy || !selectedAsset}
              onClick={() => void analyze()}
            >
              {pending && !archive ? t("github.analyzing") : t("github.analyze")}
            </Button>
          </div>
          <div className="flex gap-2">
            <Button
              type="button"
              size="sm"
              variant={exclusionMode === "directory" ? "secondary" : "ghost"}
              onClick={() => setExclusionMode("directory")}
            >
              {t("github.byDirectory")}
            </Button>
            <Button
              type="button"
              size="sm"
              variant={exclusionMode === "file" ? "secondary" : "ghost"}
              onClick={() => setExclusionMode("file")}
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
                      onChange={() => {
                        setExcludeDirs((current) =>
                          toggleExclusion(current, dir),
                        );
                        setPlan(null);
                      }}
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
                      onChange={() => {
                        setExcludeFiles((current) =>
                          toggleExclusion(current, file.path),
                        );
                        setPlan(null);
                      }}
                    />
                    <span className="font-mono text-xs">{file.path}</span>
                  </label>
                ))
              )}
            </div>
          ) : null}
          {!archive ? (
            <p className="text-xs text-fg-subtle">{t("github.noItems")}</p>
          ) : null}
        </div>
      ) : null}

      <div className="flex flex-wrap gap-2">
        <Button
          type="button"
          variant="secondary"
          disabled={busy || serverId == null}
          onClick={() => void checkPlan()}
        >
          {pending && !plan ? t("checking") : t("checkPlan")}
        </Button>
        <Button
          type="button"
          disabled={busy || !plan || plan.blocked || !selectedAsset}
          onClick={() => void install()}
        >
          <Download className="size-4" />
          {pending && plan ? t("installing") : t("install")}
        </Button>
      </div>

      {plan ? <PlanSummary plan={plan} /> : null}

      {operation ? (
        <div
          className="rounded-md border border-line bg-surface-overlay/40 px-4 py-3"
          data-testid="market-install-log"
        >
          <p className="text-sm font-medium text-fg">
            {t("liveLog")} · {operation.status}
          </p>
          <p className="mt-1 text-xs text-fg-subtle">
            {pluginTitle}
            {selectedAsset ? ` · ${selectedAsset.name}` : ""}
          </p>
          <pre className="mt-2 max-h-56 overflow-auto font-mono text-xs text-fg-muted">
            {events.length === 0
              ? t("waitingLog")
              : events.map((event) => event.message).join("\n")}
          </pre>
        </div>
      ) : null}

      {showUninstall ? (
        <div
          className="space-y-3 rounded-md border border-line bg-surface-overlay/40 px-4 py-3"
          data-testid="market-uninstall"
        >
          <div>
            <p className="text-sm font-medium text-fg">
              {t("github.uninstallTitle")}
            </p>
            <p className="mt-1 text-xs text-fg-subtle">
              {t("github.uninstallHint")}
            </p>
          </div>
          <Label htmlFor={`market-uninstall-${pluginId}`}>
            {t("github.uninstallFiles")}
          </Label>
          <Textarea
            id={`market-uninstall-${pluginId}`}
            rows={3}
            value={deleteText}
            placeholder={t("github.uninstallFilesHint")}
            onChange={(event) => setDeleteText(event.target.value)}
          />
          <Button
            type="button"
            variant="outline"
            disabled={busy || serverId == null || !deleteText.trim()}
            onClick={() => void uninstall()}
          >
            <Trash2 className="size-4" />
            {pending && deleteText.trim()
              ? t("github.uninstalling")
              : t("github.uninstall")}
          </Button>
          {uninstallNotice ? (
            <p className="text-xs text-ok">{uninstallNotice}</p>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
