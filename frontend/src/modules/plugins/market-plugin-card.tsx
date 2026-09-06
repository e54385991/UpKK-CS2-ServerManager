"use client";

import { useState } from "react";
import Link from "next/link";
import type { Route } from "next";
import { useTranslations } from "next-intl";
import { Download, Puzzle } from "lucide-react";
import { DeleteMarketPluginButton } from "@/modules/plugins/delete-market-plugin-button";
import { MarketPluginEditButton } from "@/modules/plugins/market-edit-button";
import { MarketInstallDialog } from "@/modules/plugins/market-install-dialog";
import {
  PLUGIN_CATEGORIES,
  isPluginCategory,
  type MarketInstallServer,
  type MarketPlugin,
} from "@/modules/plugins/types";
import { safeUrl } from "@/shared/lib/url";
import { Badge } from "@/shared/ui/badge";
import { Button } from "@/shared/ui/button";
import { Card } from "@/shared/ui/card";
import { GithubIcon } from "@/shared/ui/github-icon";

function hrefFor(pluginId: number, serverId?: number): Route {
  const query = serverId ? `?serverId=${serverId}` : "";
  return `/plugins/${pluginId}${query}` as Route;
}

export function MarketPluginCard({
  plugin,
  excerpt,
  servers,
  defaultServerId,
  canDelete = false,
  canEdit = false,
}: {
  plugin: MarketPlugin;
  /**
   * Description flattened to plain text by the catalog. A clamped card would
   * otherwise spend its three visible lines on `#` and fence markers, and the
   * remark pipeline stays out of the client bundle this way.
   */
  excerpt: string;
  servers: readonly MarketInstallServer[];
  defaultServerId?: number;
  canDelete?: boolean;
  canEdit?: boolean;
}) {
  const t = useTranslations("plugins");
  const [open, setOpen] = useState(false);
  const categoryLabel = isPluginCategory(plugin.category)
    ? t(`categories.${plugin.category}`)
    : plugin.category;
  const repositoryHref = safeUrl(plugin.githubUrl);

  return (
    <Card className="flex h-full flex-col p-5 transition-colors hover:border-line-strong hover:bg-surface-raised">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 space-y-1">
          <Link
            href={hrefFor(plugin.id, defaultServerId)}
            className="block truncate text-sm font-semibold text-fg hover:text-primary hover:underline"
          >
            {plugin.title}
          </Link>
          <p className="truncate text-xs text-fg-subtle">
            {plugin.author || t("unknownAuthor")}
            {plugin.version ? ` · ${plugin.version}` : ""}
          </p>
        </div>
        <div className="flex shrink-0 flex-wrap justify-end gap-1.5">
          {plugin.aiMetadata && <Badge tone={plugin.aiMetadata.reviewed ? "ok" : "warn"}>{t(plugin.aiMetadata.reviewed ? "aiImport.reviewed" : "aiImport.needsReview")}</Badge>}
          {plugin.isRecommended ? (
            <Badge tone="primary">{t("recommended")}</Badge>
          ) : null}
          <Badge tone="info">{t(`frameworks.${plugin.framework}`)}</Badge>
          <Badge tone="neutral">{categoryLabel}</Badge>
        </div>
      </div>
      {excerpt ? (
        <p className="mt-3 line-clamp-3 text-sm text-fg-muted">{excerpt}</p>
      ) : null}
      <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-fg-subtle">
          <span className="inline-flex items-center gap-1.5">
            <Puzzle className="size-3.5" />
            {t("installs", { count: plugin.installCount })}
          </span>
          {plugin.dependencies.length > 0 ? (
            <span>
              {t("dependencyCount", { count: plugin.dependencies.length })}
            </span>
          ) : null}
        </div>
        <div className="flex flex-wrap items-center justify-end gap-2">
          {repositoryHref ? (
            <Button
              asChild
              variant="ghost"
              size="icon"
              className="size-8"
              title={t("openOnGithub")}
            >
              <a
                href={repositoryHref}
                target="_blank"
                rel="noreferrer noopener"
                aria-label={t("openOnGithub")}
                data-testid="market-github-link"
              >
                <GithubIcon />
              </a>
            </Button>
          ) : null}
          {canEdit ? <MarketPluginEditButton plugin={plugin} /> : null}
          {canDelete ? (
            <DeleteMarketPluginButton
              pluginId={plugin.id}
              pluginTitle={plugin.title}
            />
          ) : null}
          <Button
            type="button"
            size="sm"
            data-testid="market-install-open"
            onClick={() => setOpen(true)}
          >
            <Download className="size-4" />
            {t("installOnCard")}
          </Button>
        </div>
      </div>
      <MarketInstallDialog
        aiUnreviewed={Boolean(plugin.aiMetadata && !plugin.aiMetadata.reviewed)}
        open={open}
        pluginId={plugin.id}
        pluginTitle={plugin.title}
        githubUrl={plugin.githubUrl}
        servers={servers}
        defaultServerId={defaultServerId ?? null}
        onClose={() => setOpen(false)}
      />
    </Card>
  );
}
