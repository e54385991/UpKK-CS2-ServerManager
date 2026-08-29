"use client";

import { useEffect, useMemo, useState } from "react";
import { useTranslations } from "next-intl";
import { Download, Search, Trash2, TriangleAlert } from "lucide-react";
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
import { serverProxyMode } from "@/modules/servers/types";
import { trackQueuedOperation } from "@/modules/servers/activity-store";
import { confirm, notify } from "@/shared/feedback";
import {
  mergeOperationEvents,
  operationEventsUrl,
  parseOperationEvent,
} from "@/modules/servers/operation-events";
import type {
  OperationStreamEvent,
  ServerOperation,
} from "@/modules/servers/types";
import { Badge } from "@/shared/ui/badge";
import { Button } from "@/shared/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/shared/ui/card";
import { Input, Label } from "@/shared/ui/input";
import { Select } from "@/shared/ui/select";

type ServerOption = {
  readonly id: number;
  readonly name: string;
  readonly usePanelProxy?: boolean;
  readonly githubProxy?: string | null;
};

const TARGET_PRESETS = [
  "addons",
  "cfg",
  "addons/counterstrikesharp",
  "addons/counterstrikesharp/plugins",
] as const;

function formatFileSize(bytes: number): string {
  if (bytes <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  const index = Math.min(
    Math.floor(Math.log(bytes) / Math.log(1024)),
    units.length - 1,
  );
  return `${(bytes / 1024 ** index).toFixed(1)} ${units[index]}`;
}

function toggleValue(values: readonly string[], item: string): string[] {
  return values.includes(item)
    ? values.filter((value) => value !== item)
    : [...values, item];
}

export function GitHubInstallForm({
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
    const ingest = (raw: string) => {
      const event = parseOperationEvent(raw);
      if (!event) return;
      setEvents((current) => mergeOperationEvents(current, [event]));
    };
    source.onmessage = (message) => ingest(message.data);
    source.addEventListener("progress", (message: MessageEvent<string>) =>
      ingest(message.data),
    );
    source.addEventListener(
      "operation_completed",
      (message: MessageEvent<string>) => ingest(message.data),
    );
    source.addEventListener(
      "operation_failed",
      (message: MessageEvent<string>) => ingest(message.data),
    );
    return () => source.close();
  }, [operation]);

  const mappingEnabled = useCustomMapping || Boolean(plan?.mappingRequired);

  function mappingPayload() {
    if (!mappingEnabled) {
      return { excludeDirs, excludeFiles };
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

  if (servers.length === 0) {
    const empty = <p className="text-sm text-fg-muted">{t("noServers")}</p>;
    if (variant === "plain") return empty;
    return (
      <Card>
        <CardHeader>
          <div>
            <CardTitle>{t("github.title")}</CardTitle>
            <CardDescription>{t("github.help")}</CardDescription>
          </div>
        </CardHeader>
        <CardContent>{empty}</CardContent>
      </Card>
    );
  }

  const body = (
    <div className="space-y-4">
        {error ? (
          <div className="flex items-start gap-2 rounded-md border border-danger/30 bg-danger-muted/50 px-3 py-2 text-sm text-danger">
            <TriangleAlert className="mt-0.5 size-4 shrink-0" />
            <span>{error}</span>
          </div>
        ) : null}

        {servers.length > 1 || defaultServerId == null ? (
          <div>
            <Label htmlFor="github-server">{t("targetServer")}</Label>
            <Select
              id="github-server"
              value={serverId ?? ""}
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
        ) : null}

        <div
          className="rounded-md border border-line bg-surface-overlay/40 px-3 py-2 text-xs text-fg-muted"
          data-testid="github-download-mode"
        >
          <span className="font-medium text-fg">{t("github.downloadMode")}: </span>
          {proxyMode === "panel"
            ? t("github.usingPanelProxy")
            : proxyMode === "github_url"
              ? `${t("github.usingGithubProxy")} ${selectedServer?.githubProxy}`
              : t("github.usingDirect")}
        </div>

        <div>
          <Label htmlFor="github-repo">{t("github.repoUrl")}</Label>
          <div className="flex flex-col gap-2 sm:flex-row">
            <Input
              id="github-repo"
              value={repoUrl}
              placeholder={t("github.repoPlaceholder")}
              onChange={(event) => setRepoUrl(event.target.value)}
            />
            <Button
              type="button"
              variant="secondary"
              disabled={pending || !repoUrl.trim()}
              onClick={() => void fetchReleases()}
            >
              <Search className="size-4" />
              {pending && releases.length === 0
                ? t("github.fetching")
                : t("github.fetchReleases")}
            </Button>
          </div>
          <p className="mt-1 text-xs text-fg-subtle">{t("github.examples")}</p>
        </div>

        {releases.length > 0 ? (
          <div>
            <Label htmlFor="github-release">{t("github.selectRelease")}</Label>
            <Select
              id="github-release"
              value={releaseIndex ?? ""}
              onChange={(event) => {
                setReleaseIndex(
                  event.target.value === "" ? null : Number(event.target.value),
                );
                setAssetIndex(null);
                setArchive(null);
                setPlan(null);
              }}
            >
              <option value="">{t("github.selectRelease")}</option>
              {releases.map((release, index) => (
                <option key={`${release.tagName}-${index}`} value={index}>
                  {release.tagName}
                  {release.name ? ` — ${release.name}` : ""}
                </option>
              ))}
            </Select>
          </div>
        ) : null}

        {selectedRelease ? (
          <fieldset className="space-y-2">
            <legend className="text-sm font-medium text-fg-muted">
              {t("github.selectAsset")}
            </legend>
            <ul className="space-y-1.5">
              {selectedRelease.assets.map((asset, index) => (
                <li key={asset.name}>
                  <label className="flex cursor-pointer items-center justify-between gap-3 rounded-md border border-line px-3 py-2 text-sm hover:border-line-strong">
                    <span className="inline-flex items-center gap-2">
                      <input
                        type="radio"
                        name="github-asset"
                        checked={assetIndex === index}
                        onChange={() => {
                          setAssetIndex(index);
                          setArchive(null);
                          setPlan(null);
                        }}
                      />
                      <span>{asset.name}</span>
                      {asset.name.toLowerCase().includes("linux") ? (
                        <Badge tone="info">Linux</Badge>
                      ) : null}
                    </span>
                    <span className="text-xs text-fg-subtle">
                      {formatFileSize(asset.size)}
                    </span>
                  </label>
                </li>
              ))}
            </ul>
          </fieldset>
        ) : null}

        <div
          className="space-y-3 rounded-md border border-line bg-surface-overlay/40 px-4 py-3"
          data-testid="github-exclude-toggles"
        >
          <div>
            <p className="text-sm font-medium text-fg">
              {t("github.excludeTitle")}
            </p>
            <p className="mt-1 text-xs text-fg-subtle">
              {t("github.excludeHint")}
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button
              type="button"
              variant="outline"
              disabled={pending || serverId == null || !selectedAsset}
              onClick={() => void analyze()}
            >
              {pending && !archive ? t("github.analyzing") : t("github.analyze")}
            </Button>
            <div className="inline-flex rounded-md border border-line">
              <button
                type="button"
                className={`px-3 py-1.5 text-xs ${
                  exclusionMode === "directory"
                    ? "bg-primary-muted text-primary"
                    : "text-fg-muted"
                }`}
                onClick={() => setExclusionMode("directory")}
              >
                {t("github.byDirectory")}
              </button>
              <button
                type="button"
                className={`px-3 py-1.5 text-xs ${
                  exclusionMode === "file"
                    ? "bg-primary-muted text-primary"
                    : "text-fg-muted"
                }`}
                onClick={() => setExclusionMode("file")}
              >
                {t("github.byFile")}
              </button>
            </div>
          </div>
          {archive && exclusionMode === "directory" ? (
            <div className="max-h-40 space-y-1 overflow-auto text-sm">
              <p className="text-xs text-fg-subtle">
                {t("github.selectDirsToExclude")}
              </p>
              {archiveDirs.length === 0 ? (
                <p className="text-xs text-fg-subtle">{t("github.noItems")}</p>
              ) : (
                archiveDirs.map((dir) => (
                  <label key={dir} className="flex items-center gap-2">
                    <input
                      type="checkbox"
                      checked={excludeDirs.includes(dir)}
                      onChange={() => {
                        setExcludeDirs((current) => toggleValue(current, dir));
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
              <p className="text-xs text-fg-subtle">
                {t("github.selectFilesToExclude")}
              </p>
              {archiveFiles.length === 0 ? (
                <p className="text-xs text-fg-subtle">{t("github.noItems")}</p>
              ) : (
                archiveFiles.map((file) => (
                  <label key={file.path} className="flex items-center gap-2">
                    <input
                      type="checkbox"
                      checked={excludeFiles.includes(file.path)}
                      onChange={() => {
                        setExcludeFiles((current) =>
                          toggleValue(current, file.path),
                        );
                        setPlan(null);
                      }}
                    />
                    <span className="font-mono text-xs">
                      {file.path}
                      {file.size > 0 ? ` (${formatFileSize(file.size)})` : ""}
                    </span>
                  </label>
                ))
              )}
            </div>
          ) : null}
          {excludeDirs.length > 0 || excludeFiles.length > 0 ? (
            <p className="text-xs text-fg-muted">
              {t("github.selectedExclusions")}:{" "}
              {[...excludeDirs, ...excludeFiles].join(", ")}
            </p>
          ) : null}
        </div>

        <div
          className="space-y-3 rounded-md border border-line bg-surface-overlay/40 px-4 py-3"
          data-testid="github-file-mapping"
        >
          <div>
            <p className="text-sm font-medium text-fg">
              {t("github.mappingTitle")}
            </p>
            <p className="mt-1 text-xs text-fg-subtle">
              {t("github.mappingHint")}
            </p>
            <label className="mt-2 flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={mappingEnabled}
                onChange={(event) => {
                  setUseCustomMapping(event.target.checked);
                  setPlan(null);
                }}
              />
              {t("github.useCustomMapping")}
            </label>
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            <div>
              <Label htmlFor="github-source">{t("github.sourcePrefix")}</Label>
              <Select
                id="github-source"
                value={sourcePrefix}
                disabled={!mappingEnabled}
                onChange={(event) => {
                  setSourcePrefix(event.target.value);
                  setPlan(null);
                }}
              >
                <option value="">{t("github.archiveRoot")}</option>
                {archiveDirs.map((dir) => (
                  <option key={dir} value={dir}>
                    {dir}
                  </option>
                ))}
              </Select>
            </div>
            <div>
              <Label htmlFor="github-target">{t("github.targetPrefix")}</Label>
              <Select
                id="github-target"
                value={targetPrefix}
                disabled={!mappingEnabled}
                onChange={(event) => {
                  setTargetPrefix(event.target.value);
                  setPlan(null);
                }}
              >
                {TARGET_PRESETS.map((target) => (
                  <option key={target} value={target}>
                    {target}
                  </option>
                ))}
                <option value="custom">{t("github.customTarget")}</option>
              </Select>
            </div>
          </div>
          {targetPrefix === "custom" ? (
            <Input
              value={customTarget}
              placeholder="addons/your-plugin"
              onChange={(event) => {
                setCustomTarget(event.target.value);
                setPlan(null);
              }}
            />
          ) : null}
          {plan?.mapping.length ? (
            <p className="font-mono text-xs text-fg-muted">
              {plan.mapping
                .map((item) => `${item.source} → ${item.target}`)
                .join(", ")}
            </p>
          ) : null}
        </div>

        <div
          className="space-y-3 rounded-md border border-line bg-surface-overlay/40 px-4 py-3"
          data-testid="github-uninstall"
        >
          <div>
            <p className="text-sm font-medium text-fg">{t("github.uninstallTitle")}</p>
            <p className="mt-1 text-xs text-fg-subtle">{t("github.uninstallHint")}</p>
          </div>
          {archive ? (
            <div className="max-h-40 space-y-1 overflow-auto text-sm">
              {archiveFiles.length === 0 ? (
                <p className="text-xs text-fg-subtle">{t("github.noItems")}</p>
              ) : (
                archiveFiles.map((file) => (
                  <label key={file.path} className="flex items-center gap-2">
                    <input
                      type="checkbox"
                      checked={deleteFiles.includes(file.path)}
                      onChange={() =>
                        setDeleteFiles((current) => toggleValue(current, file.path))
                      }
                    />
                    <span className="font-mono text-xs">{file.path}</span>
                  </label>
                ))
              )}
            </div>
          ) : (
            <p className="text-xs text-fg-subtle">{t("github.uninstallNeedAnalyze")}</p>
          )}
          <Button
            type="button"
            variant="outline"
            disabled={pending || serverId == null || deleteFiles.length === 0}
            onClick={() => void uninstall()}
          >
            <Trash2 className="size-4" />
            {pending && deleteFiles.length > 0 && !plan
              ? t("github.uninstalling")
              : t("github.uninstall")}
          </Button>
        </div>

        {selectedAsset ? (
          <div className="flex flex-wrap gap-2">
            <Button
              type="button"
              variant="secondary"
              disabled={pending || serverId == null}
              onClick={() => void checkPlan()}
            >
              {pending && !plan ? t("checking") : t("checkPlan")}
            </Button>
            <Button
              type="button"
              disabled={
                pending ||
                !plan ||
                plan.mappingRequired ||
                plan.hardConflicts.length > 0
              }
              onClick={() => void install()}
            >
              <Download className="size-4" />
              {pending && plan ? t("installing") : t("github.install")}
            </Button>
          </div>
        ) : null}

        {archive ? (
          <div className="rounded-md border border-line bg-surface-overlay/40 px-4 py-3 text-sm">
            <p className="font-medium text-fg">{t("github.archiveTitle")}</p>
            <p className="mt-1 text-fg-muted">
              {archive.hasAddonsDir
                ? t("github.hasAddons")
                : t("github.noAddons")}
            </p>
            {archive.rootDirs.length > 0 ? (
              <p className="mt-1 font-mono text-xs text-fg-subtle">
                {archive.rootDirs.join(", ")}
              </p>
            ) : null}
          </div>
        ) : null}

        {plan ? (
          <div className="space-y-2 rounded-md border border-line bg-surface-overlay/40 px-4 py-3">
            <div className="flex flex-wrap items-center gap-2">
              <p className="text-sm font-medium text-fg">{t("planTitle")}</p>
              {plan.mappingRequired || plan.hardConflicts.length > 0 ? (
                <Badge tone="danger">{t("blocked")}</Badge>
              ) : (
                <Badge tone="ok">{t("ready")}</Badge>
              )}
            </div>
            <p className="text-sm text-fg-muted">
              {plan.releaseTag} {plan.assetName ? `· ${plan.assetName}` : ""}
            </p>
            {plan.mappingRequired ? (
              <p className="text-sm text-warn">{t("github.mappingRequired")}</p>
            ) : null}
            {plan.warnings.map((warning) => (
              <p key={warning} className="text-sm text-warn">
                {warning}
              </p>
            ))}
            {plan.hardConflicts.map((item) => (
              <p key={item.ruleId} className="text-sm text-danger">
                {t("hardConflict", { reason: item.reason, id: item.ruleId })}
              </p>
            ))}
          </div>
        ) : null}

        {operation ? (
          <div className="rounded-md border border-line bg-surface-overlay/40 px-4 py-3">
            <p className="text-sm font-medium text-fg">
              {t("github.liveLog")} · {operation.status}
            </p>
            <pre className="mt-2 max-h-48 overflow-auto font-mono text-xs text-fg-muted">
              {events.length === 0
                ? t("github.waitingLog")
                : events.map((event) => event.message).join("\n")}
            </pre>
          </div>
        ) : null}
    </div>
  );

  if (variant === "plain") {
    return body;
  }

  return (
    <Card>
      <CardHeader>
        <div>
          <CardTitle>{t("github.title")}</CardTitle>
          <CardDescription>{t("github.help")}</CardDescription>
        </div>
      </CardHeader>
      <CardContent>{body}</CardContent>
    </Card>
  );
}
