"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import type { Route } from "next";
import { useFormatter, useTranslations } from "next-intl";
import { CheckCircle2, CircleAlert, LoaderCircle, Plus, RefreshCw, Server, Trash2 } from "lucide-react";
import {
  batchDeleteInitializedHostsAction,
  deleteInitializedHostAction,
  deployFromInitializedHostAction,
  getCurrentInitializedHostOperationAction,
  startInitializedHostSshTestAction,
} from "@/modules/servers/setup-actions";
import type {
  InitializedHost,
  InitializedHostOperation,
} from "@/modules/servers/setup-api";
import { addServerAfterSetupHref } from "@/modules/servers/initialized-hosts";
import { parseOperationEvent } from "@/modules/servers/operation-events";
import { initializedHostOperationEventsUrl } from "@/modules/servers/initialized-host-operation-events";
import { trackQueuedOperation } from "@/modules/servers/activity-store";
import { subscribeVisibleEventSource } from "@/shared/lib/visible-event-source";
import { alertDialog } from "@/shared/feedback/alert-store";
import { confirm as confirmDialog } from "@/shared/feedback/confirm-store";
import { useCaptcha, CaptchaField } from "@/shared/ui/captcha-field";
import { Badge } from "@/shared/ui/badge";
import { Button } from "@/shared/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/shared/ui/card";
import { Dialog } from "@/shared/ui/dialog";
import { Input, Label } from "@/shared/ui/input";
import { cn } from "@/shared/lib/cn";

type OperationMap = Record<string, InitializedHostOperation>;
type DeployTarget = InitializedHost | null;

function trackInitializedHostOperation(
  host: InitializedHost,
  operation: InitializedHostOperation,
): void {
  if (!isActive(operation.status)) return;
  trackQueuedOperation(
    {
      operationId: operation.operationId,
      serverId: -operation.initializedServerId,
      action: "test_initialized_ssh",
      status: operation.status,
      success: operation.success,
      message: operation.message,
      serverStatus: null,
      startedAt: operation.startedAt,
      completedAt: operation.completedAt,
      actorUserId: operation.actorUserId,
      streamUrl: operation.streamUrl,
      command: operation.command,
    },
    { serverName: host.name },
  );
}

export function InitializedHostsManager({ hosts: initialHosts }: { hosts: InitializedHost[] }) {
  const t = useTranslations("initializedHosts");
  const tSetup = useTranslations("setupWizard");
  const format = useFormatter();
  const router = useRouter();
  const [hosts, setHosts] = useState(initialHosts);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [operations, setOperations] = useState<OperationMap>({});
  const [deleting, setDeleting] = useState(false);
  const [testingKey, setTestingKey] = useState<string | null>(null);
  const [deployTarget, setDeployTarget] = useState<DeployTarget>(null);
  const [deployName, setDeployName] = useState("");
  const [deployPort, setDeployPort] = useState("27015");
  const [deploying, setDeploying] = useState(false);
  const captcha = useCaptcha();

  const durableHosts = useMemo(
    () => hosts.filter((host) => /^\d+$/.test(host.key)),
    [hosts],
  );

  useEffect(() => {
    let active = true;
    void Promise.all(
      durableHosts.map(async (host) => {
        const result = await getCurrentInitializedHostOperationAction(Number(host.key));
        if (active && result.ok) {
          const operation = result.data;
          if (operation) {
            setOperations((current) => ({ ...current, [host.key]: operation }));
            trackInitializedHostOperation(host, operation);
          }
        }
      }),
    );
    return () => {
      active = false;
    };
  }, [durableHosts]);

  useEffect(() => {
    const activeOperations = Object.entries(operations).filter(
      ([key, operation]) => /^\d+$/.test(key) && isActive(operation.status),
    );
    const stops = activeOperations.map(([key, operation]) => {
      const after = { current: "0" };
      return subscribeVisibleEventSource({
        url: () => initializedHostOperationEventsUrl(Number(key), operation.operationId, after.current),
        eventTypes: ["progress", "operation_completed", "operation_failed"],
        onData: (raw) => {
          const event = parseOperationEvent(raw);
          if (!event) return;
          if (event.sequence && event.sequence !== "seed") after.current = event.sequence;
          if (event.type !== "operation_completed" && event.type !== "operation_failed") return;
          setOperations((current) => {
            const previous = current[key];
            if (!previous) return current;
            return {
              ...current,
              [key]: {
                ...previous,
                status: event.type === "operation_failed" ? "failed" : "completed",
                success: event.type !== "operation_failed",
                message: event.message,
                completedAt: event.timestamp,
              },
            };
          });
        },
      });
    });
    return () => stops.forEach((stop) => stop());
  }, [operations]);

  function toggleSelected(key: string) {
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

  function selectAll() {
    setSelected((current) =>
      current.size === hosts.length ? new Set() : new Set(hosts.map((host) => host.key)),
    );
  }

  async function deleteSelected() {
    if (selected.size === 0 || deleting) return;
    const count = selected.size;
    const confirmed = await confirmDialog({
      title: t("deleteTitle"),
      description: t("deleteHelp", { count }),
      confirmLabel: t("deleteConfirm"),
      tone: "danger",
    });
    if (!confirmed) return;
    setDeleting(true);
    const keys = [...selected];
    const numeric = keys.filter((key) => /^\d+$/.test(key)).map(Number);
    const legacy = keys.filter((key) => !/^\d+$/.test(key));
    const results = [];
    if (numeric.length > 0) results.push(await batchDeleteInitializedHostsAction(numeric));
    for (const key of legacy) results.push(await deleteInitializedHostAction(key));
    const failed = results.find((result) => !result.ok);
    setDeleting(false);
    if (failed && !failed.ok) {
      await alertDialog({ title: t("deleteFailed"), description: failed.error });
      return;
    }
    setHosts((current) => current.filter((host) => !selected.has(host.key)));
    setSelected(new Set());
    setOperations((current) => {
      const next = { ...current };
      keys.forEach((key) => delete next[key]);
      return next;
    });
    router.refresh();
  }

  async function testSsh(host: InitializedHost) {
    const id = Number(host.key);
    if (!Number.isInteger(id) || testingKey || isActive(operations[host.key]?.status)) return;
    setTestingKey(host.key);
    const result = await startInitializedHostSshTestAction(id);
    setTestingKey(null);
    if (!result.ok) {
      await alertDialog({ title: t("testFailed"), description: result.error });
      return;
    }
    setOperations((current) => ({ ...current, [host.key]: result.data }));
    trackInitializedHostOperation(host, result.data);
  }

  function openDeploy(host: InitializedHost) {
    setDeployTarget(host);
    setDeployName(`${host.name} CS2`);
    setDeployPort("27015");
  }

  async function deploy() {
    if (!deployTarget || deploying) return;
    const id = Number(deployTarget.key);
    const gamePort = Number(deployPort);
    if (!Number.isInteger(id) || !Number.isInteger(gamePort) || gamePort < 1 || gamePort > 65535) {
      await alertDialog({ title: t("deployFailed"), description: t("invalidDeployInput") });
      return;
    }
    if (captcha.enabled && (!captcha.token || !captcha.code.trim())) {
      await alertDialog({ title: t("deployFailed"), description: t("captchaRequired") });
      return;
    }
    setDeploying(true);
    const result = await deployFromInitializedHostAction(id, {
      name: deployName.trim(),
      gamePort,
      serverName: "CS2 Server",
      captchaToken: captcha.token,
      captchaCode: captcha.code.trim(),
    });
    setDeploying(false);
    if (!result.ok) {
      captcha.refresh();
      await alertDialog({ title: t("deployFailed"), description: result.error });
      return;
    }
    trackQueuedOperation(result.data.operation, { serverName: deployTarget.name });
    setDeployTarget(null);
    router.push(`/servers/${result.data.serverId}/operations` as Route);
    router.refresh();
  }

  return (
    <div className="space-y-6" data-testid="initialized-hosts-manager">
      <Card className="border-primary/20 bg-primary/5">
        <CardContent className="flex flex-wrap items-center justify-between gap-3 px-5 py-4">
          <div>
            <p className="text-sm font-medium text-fg">{t("count", { count: hosts.length })}</p>
            <p className="mt-1 text-xs text-fg-muted">{t("queueHelp")}</p>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button type="button" variant="outline" size="sm" onClick={selectAll}>
              {selected.size === hosts.length ? t("clearSelection") : t("selectAll")}
            </Button>
            <Button
              type="button"
              variant="danger"
              size="sm"
              disabled={selected.size === 0 || deleting}
              onClick={() => void deleteSelected()}
            >
              <Trash2 className="size-4" />
              {deleting ? t("deleting") : t("deleteSelected", { count: selected.size })}
            </Button>
          </div>
        </CardContent>
      </Card>

      <div className="grid gap-4 xl:grid-cols-2">
        {hosts.map((host) => {
          const operation = operations[host.key];
          const numericId = Number(host.key);
          const canQueue = Number.isInteger(numericId) && numericId > 0;
          return (
            <Card key={host.key} className="overflow-hidden" data-testid={`initialized-host-${host.key}`}>
              <CardHeader className="border-b border-line/70 pb-4">
                <div className="flex items-start gap-3">
                  <input
                    type="checkbox"
                    aria-label={t("selectHost", { name: host.name })}
                    checked={selected.has(host.key)}
                    onChange={() => toggleSelected(host.key)}
                    className="mt-1 size-4 accent-primary"
                  />
                  <div className="min-w-0 flex-1">
                    <CardTitle className="flex flex-wrap items-center gap-2">
                      <Server className="size-4 text-primary" />
                      {host.name}
                      <Badge tone="ok">{t("initialized")}</Badge>
                    </CardTitle>
                    <CardDescription className="mt-1">
                      {t("savedAt", {
                        date: host.createdAt
                          ? format.dateTime(host.createdAt * 1000, {
                              dateStyle: "medium",
                              timeStyle: "short",
                            })
                          : "—",
                      })}
                    </CardDescription>
                  </div>
                  {operation ? <OperationBadge operation={operation} t={t} /> : null}
                </div>
              </CardHeader>
              <CardContent className="space-y-4 px-5 py-4">
                <dl className="grid gap-3 text-sm sm:grid-cols-2">
                  <Info label={t("host")} value={`${host.sshUser}@${host.host}`} mono />
                  <Info label={t("sshPort")} value={String(host.sshPort)} mono />
                  <Info label={t("gameDirectory")} value={host.gameDirectory} mono wide />
                </dl>
                {operation?.message ? (
                  <p className={cn("flex items-start gap-2 text-xs", operation.success === false ? "text-danger" : "text-fg-muted")}>
                    {operation.success === false ? <CircleAlert className="mt-0.5 size-3.5 shrink-0" /> : <CheckCircle2 className="mt-0.5 size-3.5 shrink-0" />}
                    {operation.message}
                  </p>
                ) : null}
                <div className="flex flex-wrap gap-2">
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    disabled={!canQueue || Boolean(testingKey) || isActive(operation?.status)}
                    onClick={() => void testSsh(host)}
                  >
                    {testingKey === host.key ? <LoaderCircle className="size-4 animate-spin" /> : <RefreshCw className="size-4" />}
                    {isActive(operation?.status) ? t("testing") : t("testSsh")}
                  </Button>
                  <Button asChild size="sm" variant="outline">
                    <Link href={addServerAfterSetupHref({ host: host.host, initializedServerId: host.key, sshUser: host.sshUser }) as Route}>
                      <Plus className="size-4" />
                      {t("addServer")}
                    </Link>
                  </Button>
                  <Button
                    type="button"
                    size="sm"
                    disabled={!canQueue}
                    onClick={() => openDeploy(host)}
                  >
                    {t("deploy")}
                  </Button>
                </div>
              </CardContent>
            </Card>
          );
        })}
      </div>

      <Dialog
        open={deployTarget !== null}
        title={t("deployTitle")}
        description={deployTarget ? t("deployHelp", { name: deployTarget.name }) : undefined}
        closeLabel={t("close")}
        onClose={() => {
          if (!deploying) setDeployTarget(null);
        }}
        footer={
          <div className="flex justify-end gap-2">
            <Button type="button" variant="secondary" disabled={deploying} onClick={() => setDeployTarget(null)}>
              {t("cancel")}
            </Button>
            <Button type="button" disabled={deploying} onClick={() => void deploy()}>
              {deploying ? t("deploying") : t("deployConfirm")}
            </Button>
          </div>
        }
      >
        <div className="space-y-4">
          <div>
            <Label htmlFor="initialized-deploy-name">{t("serverName")}</Label>
            <Input id="initialized-deploy-name" value={deployName} maxLength={255} onChange={(event) => setDeployName(event.target.value)} />
          </div>
          <div>
            <Label htmlFor="initialized-deploy-port">{t("gamePort")}</Label>
            <Input id="initialized-deploy-port" type="number" min={1} max={65535} value={deployPort} onChange={(event) => setDeployPort(event.target.value)} />
          </div>
          <CaptchaField
            id="initialized-deploy-captcha"
            label={tSetup("fields.captcha")}
            placeholder="ABCD"
            refreshLabel={tSetup("refreshCaptcha")}
            loadingLabel={tSetup("loading")}
            captcha={captcha}
          />
          <p className="text-xs text-fg-muted">{t("deployQueueHelp")}</p>
        </div>
      </Dialog>
    </div>
  );
}

function isActive(status: InitializedHostOperation["status"] | undefined): boolean {
  return status === "queued" || status === "running";
}

function Info({ label, value, mono, wide = false }: { label: string; value: string; mono?: boolean; wide?: boolean }) {
  return (
    <div className={wide ? "sm:col-span-2" : undefined}>
      <dt className="text-xs text-fg-subtle">{label}</dt>
      <dd className={cn("mt-1 break-all text-fg", mono && "font-mono text-xs")}>{value}</dd>
    </div>
  );
}

type InitializedOperationStatus = InitializedHostOperation["status"];

function OperationBadge({
  operation,
  t,
}: {
  operation: InitializedHostOperation;
  t: (key: `status.${InitializedOperationStatus}`) => string;
}) {
  const tone = operation.status === "failed" ? "danger" : operation.status === "completed" ? "ok" : operation.status === "running" ? "info" : "warn";
  return <Badge tone={tone}>{t(`status.${operation.status}`)}</Badge>;
}
