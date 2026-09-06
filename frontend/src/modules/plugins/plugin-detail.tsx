import { AIPluginReview } from "@/modules/plugins/ai-plugin-review";
import { getTranslations } from "next-intl/server";
import { notFound } from "next/navigation";
import { ExternalLink, TriangleAlert } from "lucide-react";
import { getMarketPlugin } from "@/modules/plugins/api";
import { DeleteMarketPluginButton } from "@/modules/plugins/delete-market-plugin-button";
import { InstallForm } from "@/modules/plugins/install-form";
import { MarketPluginEditButton } from "@/modules/plugins/market-edit-button";
import type { MarketInstallServer } from "@/modules/plugins/types";
import { safeUrl } from "@/shared/lib/url";
import { Badge } from "@/shared/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/shared/ui/card";
import { GithubIcon } from "@/shared/ui/github-icon";
import { Markdown } from "@/shared/ui/markdown";
import { isPluginCategory } from "@/modules/plugins/types";

export async function PluginDetail({
  pluginId,
  serverId,
  servers,
  canDelete = false,
}: {
  pluginId: number;
  serverId: number | null;
  servers: readonly MarketInstallServer[];
  canDelete?: boolean;
}) {
  const t = await getTranslations("plugins");
  const pluginResult = await getMarketPlugin(pluginId);

  if (!pluginResult.ok && pluginResult.status === 404) notFound();

  if (!pluginResult.ok) {
    return (
      <Card className="flex items-center gap-3 border-warn/30 bg-warn-muted/40 px-5 py-4 text-sm text-warn">
        <TriangleAlert className="size-4 shrink-0" />
        <span>
          {t("fetchError", { status: pluginResult.status || "network" })}
        </span>
      </Card>
    );
  }

  const plugin = pluginResult.data;
  const categoryLabel = isPluginCategory(plugin.category)
    ? t(`categories.${plugin.category}`)
    : plugin.category;
  const repositoryHref = safeUrl(plugin.githubUrl);

  return (
    <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_minmax(20rem,24rem)]">
      <Card>
        <CardHeader>
          <div>
            <CardTitle>{plugin.title}</CardTitle>
            <CardDescription>
              {plugin.author || t("unknownAuthor")}
              {plugin.version ? ` · ${plugin.version}` : ""}
            </CardDescription>
          </div>
          <div className="flex flex-wrap items-center justify-end gap-2">
            {plugin.isRecommended ? (
              <Badge tone="primary">{t("recommended")}</Badge>
            ) : null}
            <Badge tone="info">{t(`frameworks.${plugin.framework}`)}</Badge>
            <Badge tone="neutral">{categoryLabel}</Badge>
            {canDelete ? <MarketPluginEditButton plugin={plugin} /> : null}
            {canDelete ? (
              <DeleteMarketPluginButton
                pluginId={plugin.id}
                pluginTitle={plugin.title}
                redirectToMarket
              />
            ) : null}
          </div>
        </CardHeader>
        <CardContent className="space-y-4 text-sm text-fg-muted">
          {plugin.aiMetadata && <AIPluginReview pluginId={plugin.id} initial={plugin.aiMetadata} canEdit={canDelete} />}
          {plugin.description ? <Markdown source={plugin.description} /> : null}
          {repositoryHref ? (
            <a
              href={repositoryHref}
              target="_blank"
              rel="noreferrer noopener"
              title={t("openOnGithub")}
              data-testid="plugin-github-link"
              className="inline-flex max-w-full items-center gap-2 rounded-md border border-line bg-surface-overlay px-3 py-1.5 transition-colors hover:border-line-strong hover:text-primary"
            >
              <GithubIcon className="size-4 shrink-0" />
              <span className="truncate font-mono text-xs">
                {plugin.githubUrl}
              </span>
              <ExternalLink className="size-3.5 shrink-0 opacity-70" />
              <span className="sr-only">{t("openOnGithub")}</span>
            </a>
          ) : (
            <p className="font-mono text-xs break-all text-fg-subtle">
              {plugin.githubUrl}
            </p>
          )}
          {plugin.dependencies.length > 0 ? (
            <div>
              <p className="mb-1 text-xs uppercase tracking-wide text-fg-subtle">
                {t("dependencies")}
              </p>
              <ul className="space-y-1">
                {plugin.dependencies.map((dep) => (
                  <li key={dep.id}>{dep.title}</li>
                ))}
              </ul>
            </div>
          ) : (
            <p>{t("noDependencies")}</p>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <div>
            <CardTitle>{t("installTitle")}</CardTitle>
            <CardDescription>{t("installHelp")}</CardDescription>
          </div>
        </CardHeader>
        <CardContent>
          <InstallForm
            aiUnreviewed={Boolean(plugin.aiMetadata && !plugin.aiMetadata.reviewed)}
            pluginId={plugin.id}
            pluginTitle={plugin.title}
            githubUrl={plugin.githubUrl}
            servers={servers}
            defaultServerId={serverId}
            showUninstall
          />
        </CardContent>
      </Card>
    </div>
  );
}
