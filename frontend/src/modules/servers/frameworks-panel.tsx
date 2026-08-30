"use client";

import { useTranslations } from "next-intl";
import {
  Archive,
  Ban,
  Braces,
  Layers,
  Lightbulb,
  LoaderCircle,
  Puzzle,
  Wrench,
  Zap,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import {
  FRAMEWORK_CATALOG,
  type FrameworkId,
  type FrameworkRole,
  type FrameworkSpec,
  frameworkRoleTone,
} from "@/modules/servers/frameworks";
import type { ServerOperationAction } from "@/modules/servers/types";
import { Badge } from "@/shared/ui/badge";
import { Button } from "@/shared/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/shared/ui/card";
import { cn } from "@/shared/lib/cn";

const FRAMEWORK_ICONS: Record<FrameworkId, LucideIcon> = {
  metamod: Layers,
  counterstrikesharp: Braces,
  cs2fixes: Wrench,
  swiftly: Zap,
};

const FRAMEWORK_ICON_TONE: Record<FrameworkId, string> = {
  metamod: "bg-primary-muted text-primary",
  counterstrikesharp: "bg-info-muted text-info",
  cs2fixes: "bg-ok-muted text-ok",
  swiftly: "bg-warn-muted text-warn",
};

export function FrameworksPanel({
  running,
  busyAction,
  installedKeys,
  onRun,
}: {
  running: boolean;
  busyAction: ServerOperationAction | null;
  installedKeys: readonly FrameworkId[];
  onRun: (action: ServerOperationAction) => void;
}) {
  const t = useTranslations("serverDetail");
  const installed = new Set(installedKeys);

  return (
    <div className="space-y-6">
      <Card className="border-primary/30 bg-primary-muted/20">
        <CardHeader>
          <div className="min-w-0">
            <CardTitle>{t("frameworks.baseTitle")}</CardTitle>
            <CardDescription>{t("frameworks.baseHelp")}</CardDescription>
          </div>
        </CardHeader>
      </Card>

      <div className="grid gap-3 lg:grid-cols-3">
        <GuideCard
          icon={Puzzle}
          label={t("frameworks.purposeTitle")}
          text={t("frameworks.purposeHelp")}
        />
        <GuideCard
          icon={Lightbulb}
          label={t("frameworks.recommendTitle")}
          text={t("frameworks.recommendHelp")}
          tone="ok"
        />
        <GuideCard
          icon={Ban}
          label={t("frameworks.conflictTitle")}
          text={t("frameworks.conflictHelp")}
          tone="warn"
        />
      </div>

      <div className="grid gap-3 lg:grid-cols-2">
        <PathCallout
          label={t("frameworks.recommendedLabel")}
          text={t("frameworks.recommendedPath")}
          tone="ok"
        />
        <PathCallout
          label={t("frameworks.alternateLabel")}
          text={t("frameworks.alternatePath")}
          tone="warn"
        />
      </div>

      <Card>
        <CardHeader>
          <div className="min-w-0">
            <CardTitle>{t("frameworks.stackTitle")}</CardTitle>
            <CardDescription>{t("frameworks.stackHelp")}</CardDescription>
          </div>
        </CardHeader>
        <CardContent className="space-y-5">
          <FrameworkGrid
            items={FRAMEWORK_CATALOG.filter((item) => item.id !== "swiftly")}
            installed={installed}
            running={running}
            busyAction={busyAction}
            onRun={onRun}
            numbered
          />
          <FrameworkGrid
            items={FRAMEWORK_CATALOG.filter((item) => item.id === "swiftly")}
            installed={installed}
            running={running}
            busyAction={busyAction}
            onRun={onRun}
          />

          <div className="flex flex-col gap-3 border-t border-line pt-4 sm:flex-row sm:items-center sm:justify-between">
            <p className="text-sm text-fg-muted">{t("frameworks.backupHelp")}</p>
            <Button
              type="button"
              size="sm"
              variant="outline"
              disabled={running}
              onClick={() => onRun("backup_plugins")}
            >
              {busyAction === "backup_plugins" ? (
                <LoaderCircle className="animate-spin" />
              ) : (
                <Archive />
              )}
              {t("actions.backup_plugins")}
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

function FrameworkGrid({
  items,
  installed,
  running,
  busyAction,
  onRun,
  numbered = false,
}: {
  items: readonly FrameworkSpec[];
  installed: ReadonlySet<FrameworkId>;
  running: boolean;
  busyAction: ServerOperationAction | null;
  onRun: (action: ServerOperationAction) => void;
  numbered?: boolean;
}) {
  const t = useTranslations("serverDetail");
  return (
    <ol
      className={cn(
        "grid gap-3",
        items.length >= 3
          ? "md:grid-cols-2 xl:grid-cols-3"
          : "md:grid-cols-2",
      )}
    >
      {items.map((item, index) => {
        const Icon = FRAMEWORK_ICONS[item.id];
        const liveConflicts = item.conflictsWith.filter((id) =>
          installed.has(id),
        );
        const isInstalled = installed.has(item.id);
        return (
          <li
            key={item.id}
            className={cn(
              "flex flex-col rounded-md border bg-surface-raised/40 p-4",
              liveConflicts.length > 0
                ? "border-warn/40"
                : item.role === "required"
                  ? "border-primary/35"
                  : item.role === "alternative"
                    ? "border-warn/30"
                    : "border-line",
            )}
          >
            <div className="mb-3 flex items-start gap-3">
              <span
                className={cn(
                  "inline-flex size-10 shrink-0 items-center justify-center rounded-md",
                  FRAMEWORK_ICON_TONE[item.id],
                )}
                aria-hidden
              >
                <Icon className="size-5" />
              </span>
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-1.5">
                  {numbered ? (
                    <span className="font-mono text-[11px] text-fg-subtle">
                      {index + 1}
                    </span>
                  ) : null}
                  <p className="text-sm font-semibold text-fg">
                    {t(`frameworks.items.${item.id}.name`)}
                  </p>
                </div>
                <div className="mt-1.5 flex flex-wrap gap-1.5">
                  <RoleBadge
                    role={item.role}
                    label={t(`frameworks.roles.${item.role}`)}
                  />
                  {isInstalled ? (
                    <Badge tone="ok">{t("frameworks.installed")}</Badge>
                  ) : null}
                </div>
              </div>
            </div>

            <p className="mb-3 text-sm leading-6 text-fg-muted">
              {t(`frameworks.items.${item.id}.summary`)}
            </p>
            <p className="mb-3 text-xs leading-5 text-fg-subtle">
              {t(`frameworks.items.${item.id}.purpose`)}
            </p>

            <dl className="mb-3 space-y-1.5 text-xs">
              <Fact
                label={t("frameworks.depends")}
                value={
                  item.dependsOn.length === 0
                    ? t("frameworks.none")
                    : item.dependsOn
                        .map((id) => t(`frameworks.items.${id}.name`))
                        .join(" · ")
                }
              />
              <Fact
                label={t("frameworks.conflicts")}
                value={
                  item.conflictsWith.length === 0
                    ? t("frameworks.none")
                    : item.conflictsWith
                        .map((id) => t(`frameworks.items.${id}.name`))
                        .join(" · ")
                }
                warn={item.conflictsWith.length > 0}
              />
            </dl>

            {liveConflicts.length > 0 ? (
              <p className="mb-3 rounded-md border border-warn/30 bg-warn-muted/40 px-2.5 py-2 text-xs leading-5 text-warn">
                {t("frameworks.conflictLive", {
                  name: t(`frameworks.items.${item.id}.name`),
                  other: liveConflicts
                    .map((id) => t(`frameworks.items.${id}.name`))
                    .join(" · "),
                })}
              </p>
            ) : null}

            <div className="mt-auto flex flex-wrap gap-2">
              <Button
                type="button"
                size="sm"
                variant={item.role === "required" ? "primary" : "secondary"}
                disabled={running}
                aria-label={`${t("frameworks.install")} ${t(`frameworks.items.${item.id}.name`)}`}
                onClick={() => onRun(item.install)}
              >
                {busyAction === item.install ? (
                  <LoaderCircle className="animate-spin" />
                ) : (
                  <Icon />
                )}
                {t("frameworks.install")}
              </Button>
              <Button
                type="button"
                size="sm"
                variant="outline"
                disabled={running}
                aria-label={`${t("frameworks.update")} ${t(`frameworks.items.${item.id}.name`)}`}
                onClick={() => onRun(item.update)}
              >
                {busyAction === item.update ? (
                  <LoaderCircle className="animate-spin" />
                ) : null}
                {t("frameworks.update")}
              </Button>
            </div>
          </li>
        );
      })}
    </ol>
  );
}

function RoleBadge({ role, label }: { role: FrameworkRole; label: string }) {
  return <Badge tone={frameworkRoleTone(role)}>{label}</Badge>;
}

function Fact({
  label,
  value,
  warn = false,
}: {
  label: string;
  value: string;
  warn?: boolean;
}) {
  return (
    <div className="flex flex-wrap gap-x-2">
      <dt className="text-fg-subtle">{label}</dt>
      <dd className={warn ? "text-warn" : "text-fg"}>{value}</dd>
    </div>
  );
}

function GuideCard({
  icon: Icon,
  label,
  text,
  tone = "neutral",
}: {
  icon: LucideIcon;
  label: string;
  text: string;
  tone?: "neutral" | "ok" | "warn";
}) {
  return (
    <div
      className={cn(
        "rounded-lg border px-4 py-3",
        tone === "ok"
          ? "border-ok/25 bg-ok-muted/25"
          : tone === "warn"
            ? "border-warn/30 bg-warn-muted/25"
            : "border-line bg-surface",
      )}
    >
      <p
        className={cn(
          "mb-1.5 inline-flex items-center gap-1.5 text-xs font-medium uppercase tracking-wide",
          tone === "ok" ? "text-ok" : tone === "warn" ? "text-warn" : "text-fg-subtle",
        )}
      >
        <Icon className="size-3.5" />
        {label}
      </p>
      <p className="text-sm leading-6 text-fg-muted">{text}</p>
    </div>
  );
}

function PathCallout({
  label,
  text,
  tone,
}: {
  label: string;
  text: string;
  tone: "ok" | "warn";
}) {
  return (
    <div
      className={cn(
        "rounded-md border px-3 py-3 text-sm leading-6",
        tone === "ok"
          ? "border-ok/25 bg-ok-muted/30 text-fg"
          : "border-warn/30 bg-warn-muted/30 text-fg",
      )}
    >
      <p
        className={cn(
          "mb-1 text-xs font-medium uppercase tracking-wide",
          tone === "ok" ? "text-ok" : "text-warn",
        )}
      >
        {label}
      </p>
      <p className="text-fg-muted">{text}</p>
    </div>
  );
}
