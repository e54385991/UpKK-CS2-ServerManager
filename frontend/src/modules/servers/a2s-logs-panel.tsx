"use client";

import { useMemo, useState } from "react";
import { useFormatter, useTranslations } from "next-intl";
import { ChevronLeft, ChevronRight, RefreshCw } from "lucide-react";
import { paginateA2SLogs } from "@/modules/servers/a2s";
import { listMonitoringLogsAction } from "@/modules/servers/actions";
import type { MonitoringLog } from "@/modules/servers/types";
import { Button } from "@/shared/ui/button";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/shared/ui/card";

export function ServerA2SLogsPanel({
  serverId,
  initialLogs,
}: {
  serverId: number;
  initialLogs: readonly MonitoringLog[];
}) {
  const t = useTranslations("serverMonitoring");
  const format = useFormatter();
  const [logs, setLogs] = useState<readonly MonitoringLog[]>(initialLogs);
  const [logPage, setLogPage] = useState(0);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const logPageView = useMemo(
    () => paginateA2SLogs(logs, logPage),
    [logPage, logs],
  );

  async function refreshLogs() {
    setPending(true);
    const result = await listMonitoringLogsAction(serverId, "a2s_check");
    setPending(false);
    if (!result.ok) {
      setError(result.error || t("failed"));
      return;
    }
    setError(null);
    setLogs(result.data);
    setLogPage(0);
  }

  return (
    <Card data-testid="a2s-logs-panel">
      <CardHeader>
        <div>
          <CardTitle>{t("a2sLogs")}</CardTitle>
        </div>
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={pending}
          onClick={() => void refreshLogs()}
        >
          <RefreshCw />
          {t("refreshLogs")}
        </Button>
      </CardHeader>
      <CardContent>
        {error ? <p className="mb-3 text-sm text-danger">{error}</p> : null}
        {logs.length === 0 ? (
          <p className="text-sm text-fg-muted">{t("noA2sLogs")}</p>
        ) : (
          <div data-testid="a2s-logs">
            <ul className="space-y-3">
              {logPageView.items.map((log) => (
                <li key={log.id} className="text-sm">
                  <p className="text-fg">{log.message}</p>
                  <p className="text-xs text-fg-subtle">
                    {log.status}
                    {log.createdAt
                      ? ` · ${format.dateTime(new Date(log.createdAt), {
                          dateStyle: "medium",
                          timeStyle: "medium",
                        })}`
                      : ""}
                  </p>
                </li>
              ))}
            </ul>
            <div className="mt-4 flex items-center justify-between gap-3 border-t border-line pt-3 text-xs text-fg-muted">
              <span className="tabular-nums" data-testid="a2s-logs-page-info">
                {t("logPageInfo", {
                  from: logPageView.from,
                  to: logPageView.to,
                  total: logPageView.total,
                })}
              </span>
              <div className="flex items-center gap-2">
                <Button
                  type="button"
                  variant="outline"
                  size="icon"
                  data-testid="a2s-logs-prev"
                  disabled={!logPageView.hasPrev}
                  aria-label={t("logPrev")}
                  onClick={() => setLogPage(logPageView.page - 1)}
                >
                  <ChevronLeft className="size-4" />
                </Button>
                <Button
                  type="button"
                  variant="outline"
                  size="icon"
                  data-testid="a2s-logs-next"
                  disabled={!logPageView.hasNext}
                  aria-label={t("logNext")}
                  onClick={() => setLogPage(logPageView.page + 1)}
                >
                  <ChevronRight className="size-4" />
                </Button>
              </div>
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
