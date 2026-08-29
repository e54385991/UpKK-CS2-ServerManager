"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import {
  Cloud,
  LoaderCircle,
  RefreshCw,
  RotateCcw,
  TriangleAlert,
} from "lucide-react";
import { listS3BackupsAction } from "@/modules/servers/actions";
import { OperationLiveLog } from "@/modules/servers/operation-live-log";
import { useOperationRunner } from "@/modules/servers/use-operation-runner";
import type {
  DeploymentLock,
  DeploymentLogEntry,
  S3BackupItem,
  S3BackupList,
  ServerOperation,
  ServerStatus,
} from "@/modules/servers/types";
import { Button } from "@/shared/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/shared/ui/card";

function formatBackupTime(value: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleString();
}

function formatBackupSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) {
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  }
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(1)} GB`;
}

export function S3BackupsConsole({
  serverId,
  serverStatus,
  initialBackups,
  initialOperation,
  initialLogs,
  initialLock,
}: {
  serverId: number;
  serverStatus: ServerStatus;
  initialBackups: S3BackupList;
  initialOperation: ServerOperation | null;
  initialLogs: DeploymentLogEntry[];
  initialLock: DeploymentLock;
}) {
  const t = useTranslations("s3Backups");
  const [backups, setBackups] = useState(initialBackups);
  const [refreshing, setRefreshing] = useState(false);
  const {
    operation,
    events,
    logRef,
    running,
    busyAction,
    error,
    streamFailed,
    canForceStop,
    runS3Restore,
    refreshAfterForceStop,
  } = useOperationRunner({
    serverId,
    serverStatus,
    initialOperation,
    initialLogs,
    initialLock,
  });
  const emptyHint = useMemo(() => t("streamEmpty"), [t]);
  const restoring = running || busyAction === "s3_restore";

  async function refreshList() {
    setRefreshing(true);
    const result = await listS3BackupsAction(serverId);
    setRefreshing(false);
    if (result.ok) setBackups(result.data);
  }

  async function onRestore(item: S3BackupItem) {
    if (restoring) return;
    if (!window.confirm(t("confirmRestore", { name: item.filename }))) {
      return;
    }
    await runS3Restore(item.key);
    await refreshList();
  }

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <div className="min-w-0">
            <CardTitle>{t("title")}</CardTitle>
            <CardDescription>{t("help")}</CardDescription>
          </div>
          <Button
            type="button"
            size="sm"
            variant="outline"
            disabled={refreshing}
            onClick={() => void refreshList()}
          >
            {refreshing ? (
              <LoaderCircle className="animate-spin" />
            ) : (
              <RefreshCw />
            )}
            {t("refresh")}
          </Button>
        </CardHeader>
        <CardContent className="space-y-4">
          {!backups.configured ? (
            <div className="flex items-start gap-3 rounded-md border border-warn/30 bg-warn-muted/40 px-3 py-3 text-sm text-warn">
              <TriangleAlert className="mt-0.5 size-4 shrink-0" />
              <div className="space-y-2">
                <p>{backups.message || t("notConfigured")}</p>
                <p className="text-fg-muted">{t("notConfiguredHelp")}</p>
                <Link
                  href="/settings/profile"
                  className="inline-flex text-sm font-medium text-primary hover:underline"
                >
                  {t("goProfile")}
                </Link>
              </div>
            </div>
          ) : null}

          {backups.configured && backups.items.length === 0 ? (
            <p className="flex items-center gap-2 text-sm text-fg-muted">
              <Cloud className="size-4 text-fg-subtle" />
              {t("empty")}
            </p>
          ) : null}

          {backups.items.length > 0 ? (
            <div className="overflow-x-auto">
              <table className="w-full text-left text-sm">
                <thead className="text-xs uppercase tracking-wide text-fg-subtle">
                  <tr>
                    <th className="pb-2 pr-4 font-medium">{t("file")}</th>
                    <th className="pb-2 pr-4 font-medium">{t("size")}</th>
                    <th className="pb-2 pr-4 font-medium">{t("time")}</th>
                    <th className="pb-2 text-right font-medium">{t("actions")}</th>
                  </tr>
                </thead>
                <tbody>
                  {backups.items.map((item) => (
                    <tr key={item.key} className="border-t border-line">
                      <td className="py-3 pr-4 font-medium text-fg">
                        {item.filename}
                      </td>
                      <td className="py-3 pr-4 text-fg-muted">
                        {formatBackupSize(item.size)}
                      </td>
                      <td className="py-3 pr-4 text-fg-muted">
                        {formatBackupTime(item.lastModified)}
                      </td>
                      <td className="py-3 text-right">
                        <Button
                          type="button"
                          size="sm"
                          variant="outline"
                          disabled={restoring}
                          onClick={() => void onRestore(item)}
                        >
                          {busyAction === "s3_restore" ? (
                            <LoaderCircle className="animate-spin" />
                          ) : (
                            <RotateCcw />
                          )}
                          {t("restore")}
                        </Button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}
        </CardContent>
      </Card>

      {error ? (
        <p className="rounded-md border border-danger/30 bg-danger-muted/40 px-3 py-2 text-sm text-danger">
          {error}
        </p>
      ) : null}

      <OperationLiveLog
        serverId={serverId}
        operation={operation}
        events={events}
        logRef={logRef}
        streamFailed={streamFailed}
        canForceStop={canForceStop}
        emptyHint={emptyHint}
        description={t("streamHelp")}
        onForceStopDone={refreshAfterForceStop}
      />
    </div>
  );
}
