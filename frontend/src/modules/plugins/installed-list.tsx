"use client";

import Link from "next/link";
import type { Route } from "next";
import { useState, useTransition } from "react";
import { useTranslations } from "next-intl";
import { Eraser, Trash2 } from "lucide-react";
import {
  forgetAllServerPluginsAction,
  forgetServerPluginAction,
} from "@/modules/plugins/actions";
import type { ManagedPlugin } from "@/modules/plugins/types";
import { confirm, notify } from "@/shared/feedback";
import { Badge } from "@/shared/ui/badge";
import { Button } from "@/shared/ui/button";

/**
 * Installed-plugin records with per-row and bulk "clear record" controls.
 *
 * Clearing is bookkeeping only. It removes the panel's tracking row so version
 * checks and auto-updates stop; the plugin files on the game server stay where
 * they are. Uninstalling files is a separate, queued operation.
 */
export function InstalledPluginsList({
  serverId,
  plugins,
}: {
  serverId: number;
  plugins: readonly ManagedPlugin[];
}) {
  const t = useTranslations("plugins");
  const [pending, start] = useTransition();
  const [busyId, setBusyId] = useState<number | null>(null);

  function forgetOne(plugin: ManagedPlugin) {
    start(async () => {
      const confirmed = await confirm({
        title: t("forgetOneTitle", { name: plugin.displayName }),
        description: t("forgetNotice"),
        confirmLabel: t("forgetConfirm"),
        tone: "danger",
      });
      if (!confirmed) return;
      setBusyId(plugin.id);
      const result = await forgetServerPluginAction(serverId, plugin.id);
      setBusyId(null);
      if (result.ok) notify.success(t("forgetOneDone", { name: plugin.displayName }));
      else notify.error(result.error || t("forgetFailed"));
    });
  }

  function forgetAll() {
    start(async () => {
      const confirmed = await confirm({
        title: t("forgetAllTitle", { count: plugins.length }),
        description: t("forgetNotice"),
        confirmLabel: t("forgetConfirm"),
        tone: "danger",
      });
      if (!confirmed) return;
      const result = await forgetAllServerPluginsAction(serverId);
      if (result.ok) notify.success(t("forgetAllDone", { count: plugins.length }));
      else notify.error(result.error || t("forgetFailed"));
    });
  }

  return (
    <div className="space-y-3">
      <p className="rounded-md border border-line bg-surface-raised/40 px-3 py-2 text-xs text-fg-muted">
        {t("forgetNotice")}
      </p>
      <ul className="divide-y divide-line">
        {plugins.map((plugin) => (
          <li
            key={plugin.id}
            className="flex flex-wrap items-start justify-between gap-3 py-3 first:pt-0"
          >
            <div className="min-w-0 space-y-1">
              <p className="truncate text-sm font-medium text-fg">{plugin.displayName}</p>
              <p className="text-xs text-fg-subtle">
                {plugin.installedVersion}
                {plugin.latestVersion && plugin.latestVersion !== plugin.installedVersion
                  ? ` → ${plugin.latestVersion}`
                  : ""}
              </p>
            </div>
            <div className="flex flex-wrap items-center gap-1.5">
              <Badge tone="neutral">{plugin.sourceType}</Badge>
              {plugin.marketPluginId ? (
                <Link
                  href={`/plugins/${plugin.marketPluginId}?serverId=${serverId}` as Route}
                  className="text-xs text-primary hover:underline"
                >
                  {t("viewInMarket")}
                </Link>
              ) : null}
              <Button
                type="button"
                variant="ghost"
                size="sm"
                disabled={pending}
                onClick={() => forgetOne(plugin)}
              >
                <Trash2 className="size-3.5" />
                {busyId === plugin.id ? t("forgetPending") : t("forgetOne")}
              </Button>
            </div>
          </li>
        ))}
      </ul>
      <Button
        type="button"
        variant="outline"
        size="sm"
        disabled={pending || plugins.length === 0}
        onClick={forgetAll}
      >
        <Eraser className="size-3.5" />
        {t("forgetAll")}
      </Button>
    </div>
  );
}
