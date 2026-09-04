"use client";

import { useEffect, useRef, useState } from "react";
import { useFormatter, useTranslations } from "next-intl";
import type { Route } from "next";
import {
  applySystemCleanupAction,
  deleteCleanupAction,
  updateCleanupPolicyAction,
} from "@/modules/cleanup/actions";
import {
  cleanupScanStreamUrl,
  cleanupSystemStreamUrl,
  openCleanupEventSource,
} from "@/modules/cleanup/stream";
import {
  CLEANUP_SYSTEM_TARGETS,
  type CleanupItem,
  type CleanupPolicy,
  type CleanupScan,
  type CleanupSystemScan,
  type CleanupSystemTargetId,
} from "@/modules/cleanup/types";
import {
  toCleanupScan,
  toCleanupSystemScan,
  type CleanupScanViewDto,
  type CleanupSystemScanDto,
} from "@/modules/cleanup/wire";
import { trackQueuedOperation } from "@/modules/servers/activity-store";
import { useQueuedOperationTerminal } from "@/modules/servers/use-queued-operation-terminal";
import { confirm } from "@/shared/feedback";
import { Button } from "@/shared/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/shared/ui/card";
import { Input, Label } from "@/shared/ui/input";
import { LinkButton } from "@/shared/ui/link-button";
import { Skeleton } from "@/shared/ui/skeleton";
import { Switch } from "@/shared/ui/switch";
import { Badge } from "@/shared/ui/badge";

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function ItemList({ items, limit = 20 }: { items: readonly CleanupItem[]; limit?: number }) {
  const shown = items.slice(0, limit);
  return (
    <ul className="max-h-40 space-y-1 overflow-auto text-xs text-fg-muted">
      {shown.map((item) => (
        <li key={item.path} className="break-all">
          {item.path} · {formatSize(item.size)}
        </li>
      ))}
      {items.length > limit ? <li>…</li> : null}
    </ul>
  );
}

function CommandBlock({
  title,
  lines,
  copyLabel,
  copiedLabel,
}: {
  title: string;
  lines: readonly string[];
  copyLabel: string;
  copiedLabel: string;
}) {
  const [copied, setCopied] = useState(false);
  if (lines.length === 0) return null;
  return (
    <div className="space-y-2 rounded-md border border-line bg-surface-raised p-3">
      <div className="flex items-center justify-between gap-2">
        <p className="text-xs font-medium text-fg">{title}</p>
        <Button
          type="button"
          size="sm"
          variant="outline"
          onClick={() => {
            void navigator.clipboard.writeText(lines.join("\n")).then(() => {
              setCopied(true);
              window.setTimeout(() => setCopied(false), 1500);
            });
          }}
        >
          {copied ? copiedLabel : copyLabel}
        </Button>
      </div>
      <pre className="max-h-40 overflow-auto whitespace-pre-wrap break-all font-mono text-[11px] text-fg-muted">
        {lines.join("\n")}
      </pre>
    </div>
  );
}

export function CleanupConsole({
  serverId,
  initialPolicy,
}: {
  serverId: number;
  initialPolicy: CleanupPolicy | null;
}) {
  const t = useTranslations("cleanup");
  const format = useFormatter();
  const [scan, setScan] = useState<CleanupScan | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [workshopConfirm, setWorkshopConfirm] = useState("");
  const [pending, setPending] = useState<string | null>(null);
  const [banner, setBanner] = useState<string | null>(null);
  const [queuedOperationId, setQueuedOperationId] = useState<string | null>(null);
  const queuedKindRef = useRef<"delete" | "system" | null>(null);
  const [scanProgress, setScanProgress] = useState<string | null>(null);
  const streamCancelRef = useRef<(() => void) | null>(null);
  const [systemScan, setSystemScan] = useState<CleanupSystemScan | null>(null);
  const [systemSelected, setSystemSelected] = useState<string[]>([]);
  const [policy, setPolicy] = useState<CleanupPolicy | null>(initialPolicy);
  const [policyEnabled, setPolicyEnabled] = useState(initialPolicy?.enabled ?? false);
  const [retainDays, setRetainDays] = useState(String(initialPolicy?.retainDays ?? 7));
  const [scheduleValue, setScheduleValue] = useState(
    initialPolicy?.scheduleValue ?? "03:30",
  );
  const [policyTargets, setPolicyTargets] = useState<string[]>(
    [...(initialPolicy?.targets ?? ["game_logs"])],
  );
  const hostConfigHref = `/servers/${serverId}/host-config` as Route;
  const queued = Boolean(queuedOperationId);

  function closeStream() {
    streamCancelRef.current?.();
    streamCancelRef.current = null;
  }

  useEffect(() => {
    return () => {
      streamCancelRef.current?.();
      streamCancelRef.current = null;
    };
  }, []);

  useQueuedOperationTerminal(queuedOperationId, serverId, (status, message) => {
    const kind = queuedKindRef.current;
    queuedKindRef.current = null;
    setQueuedOperationId(null);
    setBanner(message || (status === "completed" ? t("queuedDone") : t("failed")));
    if (status !== "completed") return;
    if (kind === "delete") {
      setScan(null);
      setSelected([]);
    } else if (kind === "system") {
      setSystemScan(null);
      setSystemSelected([]);
    }
  });

  function phaseText(phase: unknown, fallback: string): string {
    const known = {
      safe_roots: t("phase.safe_roots"),
      logs: t("phase.logs"),
      archives: t("phase.archives"),
      workshop: t("phase.workshop"),
      privilege: t("phase.privilege"),
      game_logs: t("phase.game_logs"),
      thumbnails: t("phase.thumbnails"),
      apt_cache: t("phase.apt_cache"),
      journal: t("phase.journal"),
      tmp: t("phase.tmp"),
      crash: t("phase.crash"),
      rotated_logs: t("phase.rotated_logs"),
    } as const;
    if (typeof phase === "string" && phase in known) {
      return known[phase as keyof typeof known];
    }
    return fallback || t("scanning");
  }

  function runScan() {
    closeStream();
    setPending("scan");
    setBanner(null);
    setScan(null);
    setSelected([]);
    setScanProgress(t("scanning"));
    streamCancelRef.current = openCleanupEventSource(cleanupScanStreamUrl(serverId), {
      streamFailedMessage: t("streamFailed"),
      streamClosedMessage: t("streamClosed"),
      onPhase: (message) => {
        setScanProgress(message || t("scanning"));
      },
      onEvent: (type, data) => {
        if (type === "phase") {
          setScanProgress(phaseText(data.phase, typeof data.message === "string" ? data.message : ""));
          return;
        }
        if (type === "batch") {
          const found = Number(data.found) || 0;
          const categories = {
            safe: t("category.safe"),
            archive: t("category.archive"),
            workshop: t("category.workshop"),
          } as const;
          const category =
            typeof data.category === "string" && data.category in categories
              ? categories[data.category as keyof typeof categories]
              : "";
          setScanProgress(
            `${phaseText(data.phase, "")}${category ? ` · ${category}` : ""} · ${t("scanFound", { found })}`,
          );
        }
      },
      onDone: (data) => {
        streamCancelRef.current = null;
        setPending(null);
        setScanProgress(null);
        setScan(toCleanupScan(data as CleanupScanViewDto));
        setSelected([]);
      },
      onError: (message) => {
        streamCancelRef.current = null;
        setPending(null);
        setScanProgress(null);
        setBanner(message || t("failed"));
      },
    });
  }

  function toggleArchive(path: string, checked: boolean) {
    setSelected((current) =>
      checked ? [...current, path] : current.filter((item) => item !== path),
    );
  }

  async function removeSafe() {
    if (!(await confirm(t("confirmSafe")))) return;
    setPending("safe");
    const result = await deleteCleanupAction(serverId, { mode: "safe" });
    setPending(null);
    if (!result.ok) {
      setBanner(result.error || t("failed"));
      return;
    }
    queuedKindRef.current = "delete";
    setQueuedOperationId(result.data.operationId);
    trackQueuedOperation(result.data);
    setBanner(t("queuedToTray"));
  }

  async function removeArchives() {
    if (selected.length === 0) return;
    if (!(await confirm(t("confirmArchives")))) return;
    setPending("archives");
    const result = await deleteCleanupAction(serverId, {
      mode: "archives",
      paths: selected,
    });
    setPending(null);
    if (!result.ok) {
      setBanner(result.error || t("failed"));
      return;
    }
    queuedKindRef.current = "delete";
    setQueuedOperationId(result.data.operationId);
    trackQueuedOperation(result.data);
    setBanner(t("queuedToTray"));
  }

  async function removeWorkshop() {
    if (!(await confirm(t("confirmWorkshop")))) return;
    setPending("workshop");
    const result = await deleteCleanupAction(serverId, {
      mode: "workshop",
      confirmationText: workshopConfirm,
    });
    setPending(null);
    if (!result.ok) {
      setBanner(result.error || t("failed"));
      return;
    }
    queuedKindRef.current = "delete";
    setQueuedOperationId(result.data.operationId);
    trackQueuedOperation(result.data);
    setBanner(t("queuedToTray"));
  }

  function runSystemScan() {
    closeStream();
    setPending("system-scan");
    setBanner(null);
    setSystemScan(null);
    setScanProgress(t("systemScanning"));
    streamCancelRef.current = openCleanupEventSource(cleanupSystemStreamUrl(serverId), {
      streamFailedMessage: t("streamFailed"),
      streamClosedMessage: t("streamClosed"),
      onPhase: (message) => {
        setScanProgress(message || t("systemScanning"));
      },
      onEvent: (type, data) => {
        if (type === "phase") {
          setScanProgress(phaseText(data.phase, typeof data.message === "string" ? data.message : ""));
        }
      },
      onDone: (data) => {
        const next = toCleanupSystemScan(data as CleanupSystemScanDto);
        streamCancelRef.current = null;
        setPending(null);
        setScanProgress(null);
        setSystemScan(next);
        setSystemSelected(next.targets.filter((item) => item.canApply).map((item) => item.id));
      },
      onError: (message) => {
        streamCancelRef.current = null;
        setPending(null);
        setScanProgress(null);
        setBanner(message || t("failed"));
      },
    });
  }

  function toggleSystem(id: string, checked: boolean) {
    setSystemSelected((current) =>
      checked ? [...current, id] : current.filter((item) => item !== id),
    );
  }

  async function cleanSystem() {
    if (systemSelected.length === 0) return;
    if (!(await confirm(t("confirmSystem")))) return;
    setPending("system-clean");
    const result = await applySystemCleanupAction(serverId, {
      targets: systemSelected,
      retainDays: Number(retainDays) || 7,
    });
    setPending(null);
    if (!result.ok) {
      setBanner(result.error || t("failed"));
      return;
    }
    queuedKindRef.current = "system";
    setQueuedOperationId(result.data.operationId);
    trackQueuedOperation(result.data);
    setBanner(t("queuedToTray"));
  }

  function togglePolicyTarget(id: string, checked: boolean) {
    setPolicyTargets((current) =>
      checked ? [...current, id] : current.filter((item) => item !== id),
    );
  }

  async function savePolicy() {
    setPending("policy");
    setBanner(null);
    const result = await updateCleanupPolicyAction(serverId, {
      enabled: policyEnabled,
      retainDays: Number(retainDays) || 7,
      scheduleValue,
      targets: policyTargets,
    });
    setPending(null);
    if (!result.ok) {
      setBanner(result.error || t("failed"));
      return;
    }
    setPolicy(result.data);
    setBanner(result.data.message || t("policySaved"));
  }

  const privilegeTone =
    systemScan?.privilege === "none"
      ? "danger"
      : systemScan?.privilege === "sudo" || systemScan?.privilege === "root"
        ? "ok"
        : "neutral";

  return (
    <div className="space-y-6" data-testid="cleanup-console">
    <Card>
      <CardHeader>
        <div>
          <CardTitle>{t("title")}</CardTitle>
          <CardDescription>{t("help")}</CardDescription>
        </div>
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={Boolean(pending)}
          onClick={() => runScan()}
        >
          {pending === "scan" ? t("scanning") : t("scan")}
        </Button>
      </CardHeader>
      <CardContent className="space-y-4">
        {banner ? (
          <p className="text-sm text-fg-muted" role="status">
            {banner}
          </p>
        ) : null}
        {pending === "scan" && scanProgress ? (
          <p className="text-sm text-fg-muted" role="status" data-testid="cleanup-scan-progress">
            {scanProgress}
          </p>
        ) : null}
        {!scan ? (
          pending === "scan" ? null : (
            <p className="text-sm text-fg-muted">{t("scanHint")}</p>
          )
        ) : (
          <div className="grid gap-4 lg:grid-cols-3">
            <p className="text-sm text-fg-muted lg:col-span-3">
              {t("total")}: {formatSize(scan.totalSize)}
            </p>
            {scan.truncated ? (
              <p className="text-xs text-warn lg:col-span-3">{t("truncatedHint")}</p>
            ) : null}
            <section className="space-y-3 rounded-md border border-line p-3">
              <h3 className="text-sm font-medium">{t("safeTitle")}</h3>
              <p className="text-xs text-fg-subtle">{t("safeHelp")}</p>
              <p className="text-xs text-fg-muted">
                {t("showingCount", {
                  shown: scan.safeItems.length,
                  total: scan.safeItemCount,
                })}
              </p>
              <ItemList items={scan.safeItems} />
              <Button
                type="button"
                size="sm"
                disabled={Boolean(pending) || queued || scan.safeItems.length === 0}
                onClick={() => void removeSafe()}
              >
                {pending === "safe" ? t("deleting") : t("cleanSafe")}
              </Button>
            </section>
            <section className="space-y-3 rounded-md border border-line p-3">
              <h3 className="text-sm font-medium">{t("archivesTitle")}</h3>
              <p className="text-xs text-fg-subtle">{t("archivesHelp")}</p>
              <p className="text-xs text-fg-muted">
                {t("showingCount", {
                  shown: scan.archiveItems.length,
                  total: scan.archiveItemCount,
                })}
              </p>
              <ul className="max-h-40 space-y-2 overflow-auto text-xs">
                {scan.archiveItems.map((item) => (
                  <li key={item.path} className="flex items-start gap-2">
                    <input
                      type="checkbox"
                      className="mt-0.5"
                      checked={selected.includes(item.path)}
                      onChange={(event) =>
                        toggleArchive(item.path, event.target.checked)
                      }
                    />
                    <span className="break-all text-fg-muted">
                      {item.path} · {formatSize(item.size)}
                    </span>
                  </li>
                ))}
              </ul>
              <Button
                type="button"
                size="sm"
                variant="secondary"
                disabled={Boolean(pending) || queued || selected.length === 0}
                onClick={() => void removeArchives()}
              >
                {pending === "archives" ? t("deleting") : t("deleteArchives")}
              </Button>
            </section>
            <section className="space-y-3 rounded-md border border-danger/30 p-3">
              <h3 className="text-sm font-medium text-danger">{t("workshopTitle")}</h3>
              <p className="text-xs text-fg-subtle">{t("workshopHelp")}</p>
              <p className="break-all font-mono text-xs text-fg-muted">
                {scan.workshopPath || "—"}
              </p>
              <p className="text-xs text-fg-muted">
                {t("items")}: {scan.workshopCount} · {formatSize(scan.workshopSize)}
              </p>
              <div className="space-y-2">
                <Label htmlFor="workshop-confirm">{t("workshopConfirmLabel")}</Label>
                <Input
                  id="workshop-confirm"
                  value={workshopConfirm}
                  onChange={(event) => setWorkshopConfirm(event.target.value)}
                  placeholder="DELETE WORKSHOP"
                />
              </div>
              <Button
                type="button"
                size="sm"
                variant="danger"
                disabled={
                  Boolean(pending) ||
                  queued ||
                  scan.workshopCount === 0 ||
                  workshopConfirm !== "DELETE WORKSHOP"
                }
                onClick={() => void removeWorkshop()}
              >
                {pending === "workshop" ? t("deleting") : t("deleteWorkshop")}
              </Button>
            </section>
          </div>
        )}
      </CardContent>
    </Card>

    <Card data-testid="system-cleanup">
      <CardHeader>
        <div>
          <CardTitle>{t("systemTitle")}</CardTitle>
          <CardDescription>{t("systemHelp")}</CardDescription>
        </div>
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={Boolean(pending)}
          onClick={() => runSystemScan()}
        >
          {pending === "system-scan" ? t("systemScanning") : t("systemScan")}
        </Button>
      </CardHeader>
      <CardContent className="space-y-4">
        {pending === "system-scan" && scanProgress ? (
          <p className="text-sm text-fg-muted" role="status" data-testid="cleanup-system-progress">
            {scanProgress}
          </p>
        ) : null}
        {!systemScan ? (
          pending === "system-scan" ? null : (
            <p className="text-sm text-fg-muted">{t("systemHint")}</p>
          )
        ) : (
          <div className="space-y-4">
            <div className="flex flex-wrap items-center gap-2 text-sm">
              <span className="text-fg-muted">{t("privilege")}</span>
              <Badge tone={privilegeTone}>
                {systemScan.privilege === "root"
                  ? t("privilegeRoot")
                  : systemScan.privilege === "sudo"
                    ? t("privilegeSudo")
                    : t("privilegeNone")}
              </Badge>
              <span className="text-xs text-fg-subtle">
                {t("total")}: {formatSize(systemScan.totalSize)}
              </span>
            </div>
            {systemScan.privilege === "none" ? (
              <div className="space-y-3 rounded-md border border-warn/40 bg-warn-muted/30 p-3">
                <p className="text-sm font-medium text-warn">{t("noPermissionTitle")}</p>
                <p className="text-xs text-fg-muted">{t("noPermissionHelp")}</p>
                <LinkButton href={hostConfigHref} size="sm" variant="outline">
                  {t("openHostConfig")}
                </LinkButton>
                <CommandBlock
                  title={t("manualExecute")}
                  lines={systemScan.manualExecute}
                  copyLabel={t("copyCommands")}
                  copiedLabel={t("copied")}
                />
                <CommandBlock
                  title={t("manualSetup")}
                  lines={systemScan.manualSetup}
                  copyLabel={t("copyCommands")}
                  copiedLabel={t("copied")}
                />
              </div>
            ) : null}
            <ul className="space-y-2">
              {systemScan.targets.map((item) => (
                <li key={item.id} className="flex items-start gap-2 text-sm">
                  <input
                    type="checkbox"
                    className="mt-1"
                    checked={systemSelected.includes(item.id)}
                    onChange={(event) => toggleSystem(item.id, event.target.checked)}
                  />
                  <div className="min-w-0 flex-1">
                    <p className="text-fg">{item.title}</p>
                    <p className="text-xs text-fg-subtle">{item.reason}</p>
                    <p className="text-xs text-fg-muted">
                      {formatSize(item.size)}
                      {" · "}
                      {item.canApply ? t("canRun") : t("needsRoot")}
                    </p>
                  </div>
                </li>
              ))}
            </ul>
            <Button
              type="button"
              size="sm"
              disabled={Boolean(pending) || queued || systemSelected.length === 0}
              onClick={() => void cleanSystem()}
            >
              {pending === "system-clean" ? t("deleting") : t("systemClean")}
            </Button>
          </div>
        )}
      </CardContent>
    </Card>

    <Card data-testid="cleanup-policy">
      <CardHeader>
        <div>
          <CardTitle>{t("policyTitle")}</CardTitle>
          <CardDescription>{t("policyHelp")}</CardDescription>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex items-center justify-between gap-3">
          <Label htmlFor="cleanup-policy-enabled" className="mb-0">
            {t("policyEnabled")}
          </Label>
          <Switch
            id="cleanup-policy-enabled"
            checked={policyEnabled}
            label={t("policyEnabled")}
            onCheckedChange={setPolicyEnabled}
          />
        </div>
        <div className="grid gap-3 sm:grid-cols-2">
          <div>
            <Label htmlFor="cleanup-retain-days">{t("retainDays")}</Label>
            <Input
              id="cleanup-retain-days"
              type="number"
              min={1}
              max={90}
              value={retainDays}
              onChange={(event) => setRetainDays(event.target.value)}
            />
          </div>
          <div>
            <Label htmlFor="cleanup-schedule">{t("scheduleTime")}</Label>
            <Input
              id="cleanup-schedule"
              type="time"
              value={scheduleValue}
              onChange={(event) => setScheduleValue(event.target.value)}
            />
          </div>
        </div>
        <ul className="space-y-2">
          {CLEANUP_SYSTEM_TARGETS.map((id) => (
            <li key={id} className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={policyTargets.includes(id)}
                onChange={(event) => togglePolicyTarget(id, event.target.checked)}
              />
              <span>{t(`targets.${id as CleanupSystemTargetId}`)}</span>
            </li>
          ))}
        </ul>
        <p className="text-xs text-fg-subtle">
          {t("lastRun")}:{" "}
          {policy?.lastRun
            ? format.dateTime(new Date(policy.lastRun), {
                dateStyle: "medium",
                timeStyle: "medium",
              })
            : t("neverRun")}
          {" · "}
          {t("nextRun")}:{" "}
          {policy?.nextRun
            ? format.dateTime(new Date(policy.nextRun), {
                dateStyle: "medium",
                timeStyle: "medium",
              })
            : "—"}
        </p>
        {policy?.lastError ? (
          <p className="whitespace-pre-wrap text-xs text-warn">{policy.lastError}</p>
        ) : null}
        {policy && !policy.hasSudoPassword && policyEnabled ? (
          <div className="space-y-3">
            <p className="text-xs text-fg-muted">{t("noPermissionHelp")}</p>
            <LinkButton href={hostConfigHref} size="sm" variant="outline">
              {t("openHostConfig")}
            </LinkButton>
            <CommandBlock
              title={t("manualExecute")}
              lines={policy.manualExecute}
              copyLabel={t("copyCommands")}
              copiedLabel={t("copied")}
            />
            <CommandBlock
              title={t("manualSetup")}
              lines={policy.manualSetup}
              copyLabel={t("copyCommands")}
              copiedLabel={t("copied")}
            />
          </div>
        ) : null}
        <Button
          type="button"
          size="sm"
          disabled={Boolean(pending)}
          onClick={() => void savePolicy()}
        >
          {pending === "policy" ? t("savingPolicy") : t("savePolicy")}
        </Button>
      </CardContent>
    </Card>
    </div>
  );
}

export function CleanupPanelSkeleton() {
  return (
    <div className="max-w-5xl rounded-lg border border-line bg-surface p-5 shadow-panel">
      <Skeleton className="mb-4 h-4 w-40" />
      <Skeleton className="mb-2 h-4 w-72" />
      <Skeleton className="h-48 w-full" />
    </div>
  );
}
