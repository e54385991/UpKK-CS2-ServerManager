"use client";

import { useTranslations } from "next-intl";
import { TriangleAlert } from "lucide-react";
import { runtimeMismatchValues } from "@/modules/plugins/runtime-labels";
import {
  isPluginFramework,
  type PluginInstallPlan,
} from "@/modules/plugins/types";
import { Badge } from "@/shared/ui/badge";

/**
 * Human label for a runtime key, falling back to the raw key so an unknown
 * runtime from a newer backend still renders.
 */
export function useRuntimeLabel() {
  const t = useTranslations("plugins");
  return (key: string) => (isPluginFramework(key) ? t(`frameworks.${key}`) : key);
}

export function PlanSummary({ plan }: { plan: PluginInstallPlan }) {
  const t = useTranslations("plugins");
  const label = useRuntimeLabel();
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
      {plan.framework.mismatch ? (
        <div
          role="alert"
          data-testid="plan-framework-mismatch"
          className="flex items-start gap-2 rounded-md border border-danger/40 bg-danger-muted/40 px-3 py-2 text-sm text-danger"
        >
          <TriangleAlert className="mt-0.5 size-4 shrink-0" />
          <div className="space-y-1">
            <p className="font-medium">{t("frameworkMismatchTitle")}</p>
            <p>
              {t("frameworkMismatch", runtimeMismatchValues(plan.framework, label))}
            </p>
          </div>
        </div>
      ) : null}
      {!plan.framework.mismatch && plan.framework.missing ? (
        <p className="text-sm text-warn" data-testid="plan-framework-missing">
          {t("frameworkMissing", { plugin: label(plan.framework.plugin) })}
        </p>
      ) : null}
    </div>
  );
}
