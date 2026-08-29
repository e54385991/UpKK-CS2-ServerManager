"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { MapPin, Radio, Users } from "lucide-react";
import { isA2SVersionOutdated } from "@/modules/servers/a2s";
import {
  exportServerConfigsAction,
  getBatchJournalAction,
  refreshA2SCacheAction,
  startBatchActionsAction,
  startBatchInstallPluginsAction,
  startBatchSendCommandAction,
} from "@/modules/servers/actions";
import {
  DiskSpaceRefreshButton,
  ServerDiskRefreshButton,
} from "@/modules/servers/disk-space-refresh";
import { SshHealthBlock } from "@/modules/servers/ssh-health-block";
import { confirm } from "@/shared/feedback";
import {
  BATCH_PLUGINS,
  SERVER_STATUS_TONE,
  serverProxyMode,
  type A2SCache,
  type BatchAction,
  type BatchJournal,
  type BatchPlugin,
  type DiskSpace,
  type ServerListScope,
  type ServerStatus,
  type ServerSummary,
  type SteamLatestVersion,
} from "@/modules/servers/types";
import {
  SERVER_STATUS_GROUPS,
  SERVER_WORKSPACE_CATEGORIES,
  workspaceHref,
  type ServerWorkspaceCategory,
} from "@/modules/servers/workspace";
import { Badge, StatusDot } from "@/shared/ui/badge";
import { Button } from "@/shared/ui/button";
import { Card } from "@/shared/ui/card";
import { Input } from "@/shared/ui/input";
import { cn } from "@/shared/lib/cn";

function formatGb(value: number | null): string {
  if (value == null || Number.isNaN(value)) return "—";
  return `${value.toFixed(1)} GB`;
}

function formatPercent(value: number | null): string {
  if (value == null || Number.isNaN(value)) return "—";
  return `${value.toFixed(0)}%`;
}

function downloadBundle(bundle: unknown, includeSecrets: boolean) {
  const blob = new Blob([JSON.stringify(bundle, null, 2)], {
    type: "application/json",
  });
  const url = URL.createObjectURL(blob);
  const stamp = new Date().toISOString().replace(/[:.]/g, "-");
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = `cs2-server-config-${includeSecrets ? "secrets" : "redacted"}-${stamp}.json`;
  anchor.click();
  URL.revokeObjectURL(url);
}

export function ServerFleet({
  servers,
  diskById,
  a2sById,
  steam,
  scope,
  showOwner,
}: {
  servers: readonly ServerSummary[];
  diskById: Readonly<Record<number, DiskSpace>>;
  a2sById: Readonly<Record<number, A2SCache>>;
  steam: SteamLatestVersion | null;
  scope: ServerListScope;
  showOwner: boolean;
}) {
  const t = useTranslations("servers");
  const tWorkspace = useTranslations("serverWorkspace");
  const [selected, setSelected] = useState<number[]>([]);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [journal, setJournal] = useState<BatchJournal | null>(null);
  const [command, setCommand] = useState("");
  const [plugins, setPlugins] = useState<BatchPlugin[]>([]);
  const [refreshingA2S, setRefreshingA2S] = useState(false);

  const visibleIds = useMemo(() => servers.map((server) => server.id), [servers]);
  const allSelected =
    visibleIds.length > 0 && visibleIds.every((id) => selected.includes(id));

  useEffect(() => {
    if (!journal || journal.summary.isComplete) return;
    const id = window.setInterval(() => {
      void getBatchJournalAction(journal.batchId).then((result) => {
        if (result.ok) setJournal(result.data);
      });
    }, 2000);
    return () => window.clearInterval(id);
  }, [journal]);

  function toggle(serverId: number) {
    setSelected((current) =>
      current.includes(serverId)
        ? current.filter((id) => id !== serverId)
        : [...current, serverId],
    );
  }

  function toggleAll() {
    setSelected(allSelected ? [] : visibleIds);
  }

  async function runAction(action: BatchAction) {
    if (selected.length === 0) return;
    if (
      !(await confirm(
        t("bulk.confirmAction", { action, count: selected.length }),
      ))
    ) {
      return;
    }
    setPending(true);
    setError(null);
    const result = await startBatchActionsAction(selected, action);
    setPending(false);
    if (!result.ok) {
      setError(result.error);
      return;
    }
    const journalResult = await getBatchJournalAction(result.data.batchId);
    setJournal(journalResult.ok ? journalResult.data : null);
  }

  async function runInstallPlugins() {
    if (selected.length === 0 || plugins.length === 0) return;
    setPending(true);
    setError(null);
    const result = await startBatchInstallPluginsAction(selected, plugins);
    setPending(false);
    if (!result.ok) {
      setError(result.error);
      return;
    }
    const journalResult = await getBatchJournalAction(result.data.batchId);
    setJournal(journalResult.ok ? journalResult.data : null);
  }

  async function runCommand() {
    if (selected.length === 0 || !command.trim()) return;
    setPending(true);
    setError(null);
    const result = await startBatchSendCommandAction(selected, command.trim());
    setPending(false);
    if (!result.ok) {
      setError(result.error);
      return;
    }
    const journalResult = await getBatchJournalAction(result.data.batchId);
    setJournal(journalResult.ok ? journalResult.data : null);
  }

  async function exportSelected(includeSecrets: boolean) {
    if (selected.length === 0) return;
    setPending(true);
    setError(null);
    const result = await exportServerConfigsAction({
      serverIds: selected,
      includeSecrets,
    });
    setPending(false);
    if (!result.ok) {
      setError(result.error);
      return;
    }
    downloadBundle(result.data, includeSecrets);
  }

  return (
    <div className="space-y-4">
      <div
        className="rounded-lg border border-primary/30 bg-primary-muted/20 px-4 py-3"
        data-testid="fleet-bulk-bar"
      >
        <div className="flex flex-wrap items-center justify-between gap-3">
          <label className="inline-flex items-center gap-2 text-sm text-fg">
            <input
              type="checkbox"
              checked={allSelected}
              onChange={toggleAll}
            />
            {t("bulk.selectAll")}
            <span className="text-xs text-fg-subtle">
              {t("bulk.selectedCount", { count: selected.length })}
            </span>
          </label>
          <div className="flex flex-wrap gap-2">
            <Button
              type="button"
              size="sm"
              variant="outline"
              disabled={pending || selected.length === 0}
              onClick={() => void runAction("restart")}
            >
              {t("bulk.restart")}
            </Button>
            <Button
              type="button"
              size="sm"
              variant="danger"
              disabled={pending || selected.length === 0}
              onClick={() => void runAction("stop")}
            >
              {t("bulk.stop")}
            </Button>
            <Button
              type="button"
              size="sm"
              variant="secondary"
              disabled={pending || selected.length === 0}
              onClick={() => void runAction("update")}
            >
              {t("bulk.update")}
            </Button>
            <Button
              type="button"
              size="sm"
              variant="outline"
              disabled={pending || selected.length === 0}
              onClick={() => void exportSelected(false)}
            >
              {t("bulk.exportSelected")}
            </Button>
          </div>
        </div>
        <p className="mt-2 text-xs text-fg-subtle">{t("bulk.ownerNote")}</p>
        <div className="mt-3 flex flex-wrap items-end gap-2">
          <div className="flex flex-wrap gap-3 text-xs">
            {BATCH_PLUGINS.map((plugin) => (
              <label key={plugin} className="inline-flex items-center gap-1.5">
                <input
                  type="checkbox"
                  checked={plugins.includes(plugin)}
                  onChange={() =>
                    setPlugins((current) =>
                      current.includes(plugin)
                        ? current.filter((item) => item !== plugin)
                        : [...current, plugin],
                    )
                  }
                />
                {t(`bulk.plugin.${plugin}`)}
              </label>
            ))}
          </div>
          <Button
            type="button"
            size="sm"
            disabled={pending || selected.length === 0 || plugins.length === 0}
            onClick={() => void runInstallPlugins()}
          >
            {t("bulk.installPlugins")}
          </Button>
          <Input
            value={command}
            placeholder={t("bulk.commandPlaceholder")}
            className="h-8 max-w-xs"
            onChange={(event) => setCommand(event.target.value)}
          />
          <Button
            type="button"
            size="sm"
            variant="secondary"
            disabled={pending || selected.length === 0 || !command.trim()}
            onClick={() => void runCommand()}
          >
            {t("bulk.sendCommand")}
          </Button>
        </div>
        {error ? <p className="mt-2 text-xs text-danger">{error}</p> : null}
        {journal ? (
          <p className="mt-2 text-xs text-fg-muted">
            {t("bulk.progress", {
              completed: journal.summary.completed,
              total: journal.summary.total,
              succeeded: journal.summary.succeeded,
              failed: journal.summary.failed,
            })}
          </p>
        ) : null}
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <DiskSpaceRefreshButton scope={scope} />
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={refreshingA2S}
          onClick={() => {
            setRefreshingA2S(true);
            void refreshA2SCacheAction(scope).finally(() =>
              setRefreshingA2S(false),
            );
          }}
        >
          {refreshingA2S ? t("a2s.refreshing") : t("a2s.refresh")}
        </Button>
      </div>

      {SERVER_STATUS_GROUPS.map((status) => {
        const items = servers.filter((server) => server.status === status);
        if (items.length === 0) return null;
        const categoryLabels = Object.fromEntries(
          SERVER_WORKSPACE_CATEGORIES.map((category) => [
            category,
            tWorkspace(`categories.${category}`),
          ]),
        ) as Record<ServerWorkspaceCategory, string>;
        return (
          <section key={status} className="space-y-3">
            <div className="flex items-center gap-2">
              <h2 className="text-sm font-semibold text-fg">
                {t(`status.${status}`)}
              </h2>
              <span className="text-xs text-fg-subtle">{items.length}</span>
            </div>
            <ul className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
              {items.map((server) => (
                <ServerCard
                  key={server.id}
                  server={server}
                  disk={diskById[server.id]}
                  a2s={a2sById[server.id]}
                  steam={steam}
                  selected={selected.includes(server.id)}
                  onToggle={() => toggle(server.id)}
                  showOwner={showOwner}
                  categoryLabels={categoryLabels}
                  categoryNavLabel={tWorkspace("navLabel")}
                />
              ))}
            </ul>
          </section>
        );
      })}
    </div>
  );
}

function ServerCard({
  server,
  disk,
  a2s,
  steam,
  selected,
  onToggle,
  showOwner,
  categoryLabels,
  categoryNavLabel,
}: {
  server: ServerSummary;
  disk: DiskSpace | undefined;
  a2s: A2SCache | undefined;
  steam: SteamLatestVersion | null;
  selected: boolean;
  onToggle: () => void;
  showOwner: boolean;
  categoryLabels: Record<ServerWorkspaceCategory, string>;
  categoryNavLabel: string;
}) {
  const t = useTranslations("servers");
  const tone = SERVER_STATUS_TONE[server.status];
  const livePlayers =
    a2s?.cached && a2s.success ? a2s.playerCount : null;
  const liveMax =
    a2s?.cached && a2s.success ? a2s.maxPlayers : server.maxPlayers;
  const outdated = isA2SVersionOutdated(a2s?.version, steam?.version ?? null);

  return (
    <li>
      <Card
        className={cn(
          "flex h-full flex-col p-5 transition-colors hover:border-line-strong hover:bg-surface-raised",
          selected && "border-primary/50",
        )}
      >
        <div className="flex items-start justify-between gap-3">
          <label className="flex min-w-0 items-start gap-2">
            <input
              type="checkbox"
              className="mt-1"
              checked={selected}
              onChange={onToggle}
              aria-label={server.name}
            />
            <Link href={`/servers/${server.id}`} className="min-w-0">
              <p className="truncate text-sm font-semibold text-fg">
                {server.name}
              </p>
              <p className="truncate font-mono text-xs text-fg-subtle">
                {server.host}:{server.gamePort}
              </p>
              {showOwner && server.ownerUsername ? (
                <p className="truncate text-xs text-fg-muted">
                  {server.ownerIsAdmin
                    ? t("ownerAdmin", { name: server.ownerUsername })
                    : t("owner", { name: server.ownerUsername })}
                </p>
              ) : null}
            </Link>
          </label>
          <div className="flex flex-col items-end gap-1">
            <Badge tone={tone}>
              <StatusDot tone={tone} pulse={server.status === "running"} />
              {t(`status.${server.status}`)}
            </Badge>
            <Badge
              tone={
                serverProxyMode(server) === "direct" ? "neutral" : "info"
              }
              data-testid="proxy-badge"
            >
              {t(`proxy.${serverProxyMode(server)}`)}
            </Badge>
            {server.isSshDown ? (
              <Badge tone="danger" data-testid="ssh-down-badge">
                {t("sshDown")}
              </Badge>
            ) : null}
          </div>
        </div>

        {server.description ? (
          <p className="mt-3 line-clamp-2 text-sm text-fg-muted">
            {server.description}
          </p>
        ) : null}

        <div className="mt-4 flex flex-wrap items-center gap-x-4 gap-y-2 text-xs text-fg-muted">
          <span className="inline-flex items-center gap-1.5">
            <MapPin className="size-3.5 text-fg-subtle" />
            {a2s?.mapName || server.defaultMap}
          </span>
          <span className="inline-flex items-center gap-1.5">
            <Users className="size-3.5 text-fg-subtle" />
            {livePlayers != null
              ? t("a2s.playersLive", {
                  current: livePlayers,
                  max: liveMax ?? server.maxPlayers,
                })
              : t("players", { count: server.maxPlayers })}
          </span>
          <span className="inline-flex items-center gap-1.5">
            <Radio className="size-3.5 text-fg-subtle" />
            {server.gamePort}
          </span>
        </div>

        <div
          className="mt-3 rounded-md border border-line bg-surface-overlay/50 px-3 py-2"
          data-testid="a2s-overlay"
        >
          {a2s?.cached && a2s.success ? (
            <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
              <p className="text-fg">
                {t("a2s.online")}
                {a2s.serverName ? ` · ${a2s.serverName}` : ""}
              </p>
              <span className="inline-flex items-center gap-1.5">
                {outdated ? (
                  <Badge tone="warn">{t("a2s.outdated")}</Badge>
                ) : steam?.version ? (
                  <Badge tone="ok">{t("a2s.upToDate")}</Badge>
                ) : null}
              </span>
            </div>
          ) : (
            <p className="text-xs text-fg-subtle">{t("a2s.waiting")}</p>
          )}
        </div>

        <SshHealthBlock server={server} />

        <div className="mt-3 rounded-md border border-line bg-surface-overlay/50 px-3 py-2">
          <div className="flex items-center justify-between gap-2">
            <p className="text-xs font-medium text-fg">{t("diskSpace.title")}</p>
            <ServerDiskRefreshButton serverId={server.id} />
          </div>
          <dl className="mt-1.5 grid grid-cols-3 gap-2 text-[11px] text-fg-muted">
            <div>
              <dt>{t("diskSpace.directory")}</dt>
              <dd className="font-medium text-fg">
                {formatGb(disk?.usedGb ?? null)}
              </dd>
            </div>
            <div>
              <dt>{t("diskSpace.total")}</dt>
              <dd className="font-medium text-fg">
                {formatGb(disk?.totalGb ?? null)}
              </dd>
            </div>
            <div>
              <dt>{t("diskSpace.usage")}</dt>
              <dd className="font-medium text-fg">
                {formatPercent(disk?.usedPercent ?? null)}
              </dd>
            </div>
          </dl>
          <p className="mt-1 text-[10px] text-fg-subtle">{t("diskSpace.note")}</p>
        </div>

        <nav aria-label={categoryNavLabel} className="mt-4">
          <ul className="flex flex-wrap gap-1.5">
            {SERVER_WORKSPACE_CATEGORIES.map((category) => (
              <li key={category}>
                <Link
                  href={workspaceHref(server.id, category)}
                  className={cn(
                    "inline-flex rounded-full border px-2 py-0.5 text-[11px] font-medium transition-colors",
                    category === "operations"
                      ? "border-primary/40 bg-primary-muted text-primary hover:border-primary/60"
                      : "border-line text-fg-muted hover:border-line-strong hover:text-fg",
                  )}
                >
                  {categoryLabels[category]}
                </Link>
              </li>
            ))}
          </ul>
        </nav>
      </Card>
    </li>
  );
}
