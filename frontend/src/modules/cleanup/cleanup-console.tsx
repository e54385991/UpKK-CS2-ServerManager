"use client";

import {
  CLEANUP_SYSTEM_TARGETS,
  type CleanupPolicy,
  type CleanupSystemTargetId
} from "@/modules/cleanup/types";
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
import { LinkButton } from "@/shared/ui/link-button";
import { Switch } from "@/shared/ui/switch";

import { CommandBlock, formatSize, ItemList } from "./cleanup-console-parts";
import { useCleanupConsole } from "./use-cleanup-console";

export function CleanupConsole({
  serverId,
  initialPolicy,
}: {
  serverId: number;
  initialPolicy: CleanupPolicy | null;
}) {
  const {
    t,
    format,
    scan,
    selected,
    workshopConfirm,
    setWorkshopConfirm,
    pending,
    banner,
    scanProgress,
    systemScan,
    systemSelected,
    policy,
    policyEnabled,
    setPolicyEnabled,
    retainDays,
    setRetainDays,
    scheduleValue,
    setScheduleValue,
    policyTargets,
    hostConfigHref,
    queued,
    runScan,
    toggleArchive,
    removeSafe,
    removeArchives,
    removeWorkshop,
    runSystemScan,
    toggleSystem,
    cleanSystem,
    togglePolicyTarget,
    savePolicy,
    privilegeTone,
  } = useCleanupConsole({ serverId, initialPolicy });

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

export { CleanupPanelSkeleton } from "./cleanup-console-parts";
