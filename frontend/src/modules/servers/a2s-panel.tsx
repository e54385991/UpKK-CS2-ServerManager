"use client";

import { useMemo, useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { useFormatter, useTranslations } from "next-intl";
import { ChevronLeft, ChevronRight, RefreshCw, Save, Search, TriangleAlert } from "lucide-react";
import { formatA2SDuration, paginateA2SLogs } from "@/modules/servers/a2s";
import {
  listMonitoringLogsAction,
  queryServerA2SAction,
  updateServerAction,
} from "@/modules/servers/actions";
import type { ServerDetail } from "@/modules/servers/api";
import type { A2SQuery, MonitoringLog } from "@/modules/servers/types";
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
import { Switch } from "@/shared/ui/switch";

export function ServerA2SPanel({
  server,
  initialQuery,
  initialLogs,
}: {
  server: ServerDetail;
  initialQuery: A2SQuery | null;
  initialLogs: readonly MonitoringLog[];
}) {
  const t = useTranslations("serverMonitoring");
  const format = useFormatter();
  const router = useRouter();
  const [enableA2s, setEnableA2s] = useState(server.enableA2sMonitoring);
  const [queryHost, setQueryHost] = useState(server.a2sQueryHost ?? "");
  const [queryPort, setQueryPort] = useState(
    server.a2sQueryPort != null ? String(server.a2sQueryPort) : "",
  );
  const [snapshot, setSnapshot] = useState<A2SQuery | null>(initialQuery);
  const [logs, setLogs] = useState<readonly MonitoringLog[]>(initialLogs);
  const [logPage, setLogPage] = useState(0);
  const [pending, setPending] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const lastCheckAt = snapshot?.lastUpdated || snapshot?.timestamp;
  const logPageView = useMemo(
    () => paginateA2SLogs(logs, logPage),
    [logPage, logs],
  );

  async function onSave(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const portRaw = queryPort.trim();
    setPending("save");
    setError(null);
    setNotice(null);
    const result = await updateServerAction(server.id, {
      enableA2sMonitoring: enableA2s,
      a2sFailureThreshold: Number(form.get("a2sThreshold")),
      a2sCheckIntervalSeconds: Number(form.get("a2sInterval")),
      a2sQueryHost: queryHost.trim(),
      a2sQueryPort: portRaw ? Number(portRaw) : null,
    });
    setPending(null);
    if (!result.ok) {
      setError(result.error || t("failed"));
      return;
    }
    setNotice(t("a2sSaved"));
    router.refresh();
  }

  async function queryNow() {
    setPending("query");
    setError(null);
    const result = await queryServerA2SAction(server.id, true);
    setPending(null);
    if (!result.ok) {
      setError(result.error || t("failed"));
      return;
    }
    setSnapshot(result.data);
  }

  async function refreshLogs() {
    setPending("logs");
    const result = await listMonitoringLogsAction(server.id, "a2s_check");
    setPending(null);
    if (!result.ok) {
      setError(result.error || t("failed"));
      return;
    }
    setLogs(result.data);
    setLogPage(0);
  }

  return (
    <div className="space-y-6">
      <Card data-testid="a2s-panel">
        <CardHeader>
          <div>
            <CardTitle>{t("a2sTitle")}</CardTitle>
            <CardDescription>{t("a2sHelp")}</CardDescription>
          </div>
          <Button
            type="button"
            variant="outline"
            size="sm"
            data-testid="a2s-query-now"
            disabled={pending !== null}
            onClick={() => void queryNow()}
          >
            <Search />
            {pending === "query" ? t("querying") : t("queryNow")}
          </Button>
        </CardHeader>
        <CardContent className="space-y-5">
          {enableA2s ? (
            <p className="rounded-lg border border-info/30 bg-info-muted/30 px-4 py-3 text-sm text-fg-muted">
              {t("a2sPriority")}
            </p>
          ) : null}
          <p className="text-sm text-fg-muted">{t("a2sVsSsh")}</p>

          <form onSubmit={(event) => void onSave(event)} className="space-y-5">
            <div className="grid gap-4 sm:grid-cols-2">
              <div className="space-y-1.5">
                <Label htmlFor="a2s-query-host">{t("a2sQueryHost")}</Label>
                <Input
                  id="a2s-query-host"
                  data-testid="a2s-query-host"
                  value={queryHost}
                  onChange={(event) => setQueryHost(event.target.value)}
                  placeholder={server.host}
                  autoComplete="off"
                  spellCheck={false}
                />
                <p className="text-xs text-fg-subtle">
                  {t("a2sQueryHostHint", { host: server.host })}
                </p>
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="a2s-query-port">{t("a2sQueryPort")}</Label>
                <Input
                  id="a2s-query-port"
                  data-testid="a2s-query-port"
                  type="number"
                  min={1}
                  max={65535}
                  value={queryPort}
                  onChange={(event) => setQueryPort(event.target.value)}
                  placeholder={String(server.gamePort)}
                />
                <p className="text-xs text-fg-subtle">
                  {t("a2sQueryPortHint", { port: server.gamePort })}
                </p>
              </div>
            </div>

            <div className="flex items-center justify-between gap-4">
              <Label htmlFor="enableA2s">{t("fields.enableA2s")}</Label>
              <Switch
                id="enableA2s"
                label={t("fields.enableA2s")}
                checked={enableA2s}
                onCheckedChange={setEnableA2s}
              />
            </div>
            <div className="grid gap-4 sm:grid-cols-2">
              <div className="space-y-1.5">
                <Label htmlFor="a2sInterval">{t("fields.a2sInterval")}</Label>
                <Input
                  id="a2sInterval"
                  name="a2sInterval"
                  type="number"
                  min={15}
                  max={3600}
                  required
                  defaultValue={server.a2sCheckIntervalSeconds}
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="a2sThreshold">{t("fields.a2sThreshold")}</Label>
                <Input
                  id="a2sThreshold"
                  name="a2sThreshold"
                  type="number"
                  min={1}
                  max={10}
                  required
                  defaultValue={server.a2sFailureThreshold}
                />
              </div>
            </div>

            {error ? (
              <p className="flex items-center gap-2 text-sm text-danger">
                <TriangleAlert className="size-4" />
                {error}
              </p>
            ) : null}
            {notice ? <p className="text-sm text-ok">{notice}</p> : null}

            <Button type="submit" disabled={pending !== null}>
              <Save />
              {pending === "save" ? t("a2sSaving") : t("a2sSave")}
            </Button>
          </form>

          <div
            className="grid gap-4 border-t border-line pt-5 sm:grid-cols-2"
            data-testid="a2s-last-check"
          >
            <div className="space-y-2 text-sm">
              <h3 className="font-medium text-fg">{t("querySettings")}</h3>
              <ul className="space-y-1 text-fg-muted">
                <li>
                  {t("queryHost")}:{" "}
                  <span className="font-mono text-fg">
                    {snapshot?.queryHost || queryHost || server.host}
                  </span>
                </li>
                <li>
                  {t("queryPort")}:{" "}
                  <span className="font-mono text-fg">
                    {snapshot?.queryPort || server.a2sQueryPort || server.gamePort}
                  </span>
                </li>
                <li>
                  {t("fields.enableA2s")}:{" "}
                  <Badge tone={enableA2s ? "ok" : "neutral"}>
                    {enableA2s ? t("enabled") : t("disabled")}
                  </Badge>
                </li>
                {enableA2s ? (
                  <>
                    <li>
                      {t("fields.a2sInterval")}: {server.a2sCheckIntervalSeconds}{" "}
                      {t("seconds")}
                    </li>
                    <li>
                      {t("fields.a2sThreshold")}: {server.a2sFailureThreshold}{" "}
                      {t("consecutiveFailures")}
                    </li>
                  </>
                ) : null}
              </ul>
            </div>
            <div className="space-y-2 text-sm">
              <h3 className="font-medium text-fg">{t("queryStatus")}</h3>
              {snapshot && (snapshot.cached || snapshot.live || snapshot.timestamp) ? (
                <ul className="space-y-1 text-fg-muted">
                  <li className="flex items-center gap-2">
                    {t("status")}:{" "}
                    <Badge tone={snapshot.success ? "ok" : "danger"}>
                      {snapshot.success ? t("success") : t("queryFailed")}
                    </Badge>
                  </li>
                  <li>
                    {t("lastQuery")}:{" "}
                    {lastCheckAt
                      ? format.dateTime(new Date(lastCheckAt), {
                          dateStyle: "medium",
                          timeStyle: "medium",
                        })
                      : t("neverQueried")}
                  </li>
                  {snapshot.responseTimeMs != null ? (
                    <li>{t("responseTime")}: {t("ms", { ms: snapshot.responseTimeMs })}</li>
                  ) : null}
                  {snapshot.error ? (
                    <li className="text-danger">{snapshot.error}</li>
                  ) : null}
                </ul>
              ) : (
                <p className="text-fg-muted">{t("neverQueried")}</p>
              )}
            </div>
          </div>

          {snapshot?.success && snapshot.serverInfo ? (
            <div className="space-y-3 border-t border-line pt-5">
              <h3 className="text-sm font-medium text-fg">{t("serverInformation")}</h3>
              <div className="grid gap-3 text-sm sm:grid-cols-2">
                <ul className="space-y-1 text-fg-muted">
                  <li>
                    {t("serverName")}:{" "}
                    <span className="text-fg">{snapshot.serverInfo.serverName || "—"}</span>
                  </li>
                  <li>
                    {t("currentMap")}:{" "}
                    <span className="text-fg">{snapshot.serverInfo.mapName || "—"}</span>
                  </li>
                  <li>
                    {t("game")}:{" "}
                    <span className="text-fg">{snapshot.serverInfo.game || "—"}</span>
                  </li>
                  <li>
                    {t("players")}:{" "}
                    <span className="text-fg">
                      {snapshot.serverInfo.playerCount ?? 0} /{" "}
                      {snapshot.serverInfo.maxPlayers ?? 0} ({t("bots")}:{" "}
                      {snapshot.serverInfo.botCount ?? 0})
                    </span>
                  </li>
                </ul>
                <ul className="space-y-1 text-fg-muted">
                  <li>
                    {t("passwordProtected")}:{" "}
                    {snapshot.serverInfo.passwordProtected ? t("yes") : t("no")}
                  </li>
                  <li>
                    {t("vac")}:{" "}
                    {snapshot.serverInfo.vacEnabled ? t("enabled") : t("disabled")}
                  </li>
                  <li>
                    {t("version")}:{" "}
                    <span className="font-mono text-fg">
                      {snapshot.serverInfo.version || "—"}
                    </span>
                  </li>
                  <li>
                    {t("platform")}: {snapshot.serverInfo.platform || "—"}
                  </li>
                </ul>
              </div>
              {snapshot.players.length > 0 ? (
                <div className="overflow-x-auto">
                  <p className="mb-2 text-sm text-fg-muted">
                    {t("playersOnline")} ({snapshot.players.length})
                  </p>
                  <table className="w-full text-left text-sm">
                    <thead className="text-xs text-fg-subtle">
                      <tr className="border-b border-line">
                        <th className="py-2 pr-3 font-medium">{t("playerName")}</th>
                        <th className="py-2 pr-3 font-medium">{t("score")}</th>
                        <th className="py-2 font-medium">{t("duration")}</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-line">
                      {snapshot.players.map((player, index) => (
                        <tr key={`${player.name}-${index}`}>
                          <td className="py-2 pr-3 text-fg">{player.name || "—"}</td>
                          <td className="py-2 pr-3 text-fg-muted">{player.score}</td>
                          <td className="py-2 text-fg-muted">
                            {formatA2SDuration(player.duration)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : null}
            </div>
          ) : null}

          {snapshot && !snapshot.success && (snapshot.cached || snapshot.live) ? (
            <p className="rounded-lg border border-warn/30 bg-warn-muted/30 px-4 py-3 text-sm text-warn">
              {t("queryFailedDetail")}
            </p>
          ) : null}
        </CardContent>
      </Card>

      {enableA2s ? (
        <Card>
          <CardHeader>
            <div>
              <CardTitle>{t("a2sLogs")}</CardTitle>
            </div>
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={pending !== null}
              onClick={() => void refreshLogs()}
            >
              <RefreshCw />
              {t("refreshLogs")}
            </Button>
          </CardHeader>
          <CardContent>
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
      ) : null}
    </div>
  );
}
