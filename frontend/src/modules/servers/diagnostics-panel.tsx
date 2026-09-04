"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import type { Route } from "next";
import { useTranslations } from "next-intl";
import { TriangleAlert } from "lucide-react";
import {
  executePluginDiagnosticAction,
  getLatestPluginDiagnosticAction,
  planPluginDiagnosticAction,
  restorePluginDiagnosticAction,
} from "@/modules/servers/diagnostics-actions";
import type {
  DiagnosticPlan,
  DiagnosticRecommendation,
  DiagnosticRun,
  DiagnosticScope,
} from "@/modules/servers/diagnostics-api";
import { trackQueuedOperation } from "@/modules/servers/activity-store";
import { useQueuedOperationTerminal } from "@/modules/servers/use-queued-operation-terminal";
import { Button } from "@/shared/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/shared/ui/card";
import { Select } from "@/shared/ui/select";

const SCOPES: readonly DiagnosticScope[] = [
  "both",
  "metamod",
  "counterstrikesharp",
];

const DIAGNOSTIC_REASONS = [
  "restart_loop_protection",
  "post_update_start_failures",
  "unknown",
] as const;

type DiagnosticReason = (typeof DIAGNOSTIC_REASONS)[number];

function isDiagnosticReason(value: string): value is DiagnosticReason {
  return (DIAGNOSTIC_REASONS as readonly string[]).includes(value);
}

export function PluginDiagnosticsPanel({
  serverId,
  recommendation,
}: {
  serverId: number;
  recommendation: DiagnosticRecommendation | null;
}) {
  const t = useTranslations("serverMonitoring");
  const [scope, setScope] = useState<DiagnosticScope>("both");
  const [plan, setPlan] = useState<DiagnosticPlan | null>(null);
  const [run, setRun] = useState<DiagnosticRun | null>(null);
  const [pending, setPending] = useState<"plan" | "run" | "restore" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [banner, setBanner] = useState<string | null>(null);
  const [queuedOperationId, setQueuedOperationId] = useState<string | null>(null);

  useEffect(() => {
    void getLatestPluginDiagnosticAction(serverId).then((result) => {
      if (result.ok) setRun(result.data);
    });
  }, [serverId]);

  useQueuedOperationTerminal(queuedOperationId, serverId, (status, message) => {
    setBanner(message || (status === "failed" ? message : t("queuedDone")));
    void getLatestPluginDiagnosticAction(serverId).then((result) => {
      if (result.ok) setRun(result.data);
    });
  });

  async function onPlan() {
    setPending("plan");
    setError(null);
    setBanner(null);
    const result = await planPluginDiagnosticAction(serverId, scope);
    setPending(null);
    if (!result.ok) {
      setError(result.error);
      return;
    }
    setPlan(result.data);
  }

  async function onExecute() {
    if (!plan) return;
    setPending("run");
    setError(null);
    setBanner(null);
    const result = await executePluginDiagnosticAction(
      serverId,
      scope,
      plan.planHash,
    );
    setPending(null);
    if (!result.ok) {
      setError(result.error);
      return;
    }
    trackQueuedOperation(result.data);
    setQueuedOperationId(result.data.operationId);
    setBanner(t("queuedToTray"));
  }

  async function onRestore() {
    if (!run) return;
    setPending("restore");
    setError(null);
    setBanner(null);
    const result = await restorePluginDiagnosticAction(serverId, run.id);
    setPending(null);
    if (!result.ok) {
      setError(result.error);
      return;
    }
    trackQueuedOperation(result.data);
    setQueuedOperationId(result.data.operationId);
    setBanner(t("queuedToTray"));
  }

  return (
    <div className="space-y-4" data-testid="plugin-diagnostics">
      {recommendation?.recommended ? (
        <Card className="border-warn/30 bg-warn-muted/30">
          <CardHeader>
            <div>
              <CardTitle>{t("diagnosticBannerTitle")}</CardTitle>
              <CardDescription>
                {t(
                  `diagnosticReasons.${
                    recommendation.reason && isDiagnosticReason(recommendation.reason)
                      ? recommendation.reason
                      : "unknown"
                  }`,
                )}
              </CardDescription>
            </div>
          </CardHeader>
          <CardContent className="flex flex-wrap items-center gap-3 text-sm">
            <p>
              {t("diagnosticRestarts", {
                count: recommendation.restartCount,
                max: recommendation.maxRestarts,
              })}
            </p>
            <Button asChild variant="outline">
              <Link
                href={`/assistant?prompt=crashIsolation` as Route}
                data-testid="open-diagnostic-assistant"
              >
                {t("openDiagnosticAssistant")}
              </Link>
            </Button>
          </CardContent>
        </Card>
      ) : (
        <Card data-testid="plugin-diagnostics-idle">
          <CardContent className="px-5 py-4 text-sm text-fg-muted">
            {t("diagnosticIdle")}
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader>
          <div>
            <CardTitle>{t("diagnosticTitle")}</CardTitle>
            <CardDescription>{t("diagnosticHelp")}</CardDescription>
          </div>
        </CardHeader>
        <CardContent className="space-y-3">
          {error ? (
            <div className="flex items-start gap-2 rounded-md border border-danger/30 bg-danger-muted/50 px-3 py-2 text-sm text-danger">
              <TriangleAlert className="mt-0.5 size-4 shrink-0" />
              <span>{error}</span>
            </div>
          ) : null}
          {banner && !error ? (
            <p className="text-sm text-fg-muted" data-testid="diagnostic-banner">
              {banner}
            </p>
          ) : null}
          <Select
            value={scope}
            onChange={(event) => setScope(event.target.value as DiagnosticScope)}
            aria-label={t("diagnosticScope")}
          >
            {SCOPES.map((value) => (
              <option key={value} value={value}>
                {t(`diagnosticScopes.${value}`)}
              </option>
            ))}
          </Select>
          <div className="flex flex-wrap gap-2">
            <Button
              type="button"
              variant="outline"
              disabled={pending !== null}
              onClick={() => void onPlan()}
              data-testid="diagnostic-plan"
            >
              {pending === "plan" ? t("diagnosticPlanning") : t("diagnosticPlan")}
            </Button>
            <Button
              type="button"
              disabled={plan == null || pending !== null}
              onClick={() => void onExecute()}
              data-testid="diagnostic-execute"
            >
              {pending === "run" ? t("diagnosticRunning") : t("diagnosticExecute")}
            </Button>
            <Button
              type="button"
              variant="outline"
              disabled={run == null || pending !== null}
              onClick={() => void onRestore()}
              data-testid="diagnostic-restore"
            >
              {pending === "restore"
                ? t("diagnosticRestoring")
                : t("diagnosticRestore")}
            </Button>
          </div>
          {plan ? (
            <p className="font-mono text-xs text-fg-muted">
              {t("diagnosticPlanHash")}: {plan.planHash.slice(0, 12)}… ·{" "}
              {t("diagnosticMaxStarts", { count: plan.estimatedMaxStarts })}
            </p>
          ) : null}
          {run ? (
            <p className="text-sm text-fg">
              {t("diagnosticRunStatus")}: {run.status}
              {run.error ? ` — ${run.error}` : ""}
            </p>
          ) : null}
        </CardContent>
      </Card>
    </div>
  );
}
