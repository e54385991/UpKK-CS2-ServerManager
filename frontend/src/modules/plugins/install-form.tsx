"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import type { Route } from "next";
import { useTranslations } from "next-intl";
import { Download, Trash2, TriangleAlert } from "lucide-react";
import {
  getPluginInstallPlanAction,
  installMarketPluginAction,
  uninstallMarketPluginAction,
} from "@/modules/plugins/actions";
import type { PluginInstallPlan } from "@/modules/plugins/types";
import { confirm } from "@/shared/feedback";
import { Button } from "@/shared/ui/button";
import { Label } from "@/shared/ui/input";
import { Select } from "@/shared/ui/select";
import { Badge } from "@/shared/ui/badge";
import { Textarea } from "@/shared/ui/textarea";

type ServerOption = {
  readonly id: number;
  readonly name: string;
};

export function InstallForm({
  pluginId,
  servers,
  defaultServerId,
}: {
  pluginId: number;
  servers: readonly ServerOption[];
  defaultServerId: number | null;
}) {
  const t = useTranslations("plugins");
  const router = useRouter();
  const [serverId, setServerId] = useState<number | null>(
    defaultServerId ?? servers[0]?.id ?? null,
  );
  const [plan, setPlan] = useState<PluginInstallPlan | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [deleteText, setDeleteText] = useState("");
  const [uninstallNotice, setUninstallNotice] = useState<string | null>(null);

  async function checkPlan() {
    if (serverId == null) return;
    setPending(true);
    setError(null);
    const result = await getPluginInstallPlanAction(serverId, pluginId);
    setPending(false);
    if (!result.ok) {
      setPlan(null);
      setError(result.error);
      return;
    }
    setPlan(result.data);
  }

  async function install() {
    if (serverId == null || !plan) return;
    if (plan.blocked) return;
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
      planHash: plan.planHash,
    });
    setPending(false);
    if (!result.ok) {
      setError(result.error);
      return;
    }
    router.push(`/servers/${serverId}` as Route);
    router.refresh();
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
    router.refresh();
  }

  if (servers.length === 0) {
    return (
      <p className="text-sm text-fg-muted">{t("noServers")}</p>
    );
  }

  return (
    <div className="space-y-4">
      {error ? (
        <div className="flex items-start gap-2 rounded-md border border-danger/30 bg-danger-muted/50 px-3 py-2 text-sm text-danger">
          <TriangleAlert className="mt-0.5 size-4 shrink-0" />
          <span>{error}</span>
        </div>
      ) : null}

      <div>
        <Label htmlFor="install-server">{t("targetServer")}</Label>
        <Select
          id="install-server"
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
          disabled={pending || !plan || plan.blocked}
          onClick={() => void install()}
        >
          <Download className="size-4" />
          {pending && plan ? t("installing") : t("install")}
        </Button>
      </div>

      {plan ? <PlanSummary plan={plan} /> : null}

      <div
        className="space-y-3 rounded-md border border-line bg-surface-overlay/40 px-4 py-3"
        data-testid="market-uninstall"
      >
        <div>
          <p className="text-sm font-medium text-fg">{t("github.uninstallTitle")}</p>
          <p className="mt-1 text-xs text-fg-subtle">{t("github.uninstallHint")}</p>
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
          disabled={pending || serverId == null || !deleteText.trim()}
          onClick={() => void uninstall()}
        >
          <Trash2 className="size-4" />
          {pending && deleteText.trim() ? t("github.uninstalling") : t("github.uninstall")}
        </Button>
        {uninstallNotice ? (
          <p className="text-xs text-ok">{uninstallNotice}</p>
        ) : null}
      </div>
    </div>
  );
}

function PlanSummary({ plan }: { plan: PluginInstallPlan }) {
  const t = useTranslations("plugins");
  return (
    <div className="space-y-3 rounded-md border border-line bg-surface-overlay/40 px-4 py-3">
      <div className="flex flex-wrap items-center gap-2">
        <p className="text-sm font-medium text-fg">{t("planTitle")}</p>
        {plan.blocked ? (
          <Badge tone="danger">{t("blocked")}</Badge>
        ) : (
          <Badge tone="ok">{t("ready")}</Badge>
        )}
      </div>
      <ol className="space-y-1.5 text-sm text-fg-muted">
        {plan.steps.map((step) => (
          <li key={`${step.order}-${step.pluginId}`}>
            <span className="font-mono text-xs text-fg-subtle">{step.order}.</span>{" "}
            {step.title}{" "}
            <span className="text-fg-subtle">
              (
              {step.status === "already_installed" || step.status === "install"
                ? t(`stepStatus.${step.status}`)
                : step.status}
              )
            </span>
          </li>
        ))}
      </ol>
      {plan.hardConflicts.length > 0 ? (
        <ul className="space-y-1 text-sm text-danger">
          {plan.hardConflicts.map((item) => (
            <li key={item.ruleId}>
              {t("hardConflict", { reason: item.reason, id: item.ruleId })}
            </li>
          ))}
        </ul>
      ) : null}
      {plan.warnings.length > 0 ? (
        <ul className="space-y-1 text-sm text-warn">
          {plan.warnings.map((item) => (
            <li key={item.ruleId}>
              {t("warningConflict", { reason: item.reason, id: item.ruleId })}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}