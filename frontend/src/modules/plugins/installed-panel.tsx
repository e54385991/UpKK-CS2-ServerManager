import type { Route } from "next";
import { getTranslations } from "next-intl/server";
import { Puzzle, TriangleAlert } from "lucide-react";
import { GitHubInstallForm } from "@/modules/plugins/github-install-form";
import { InstalledPluginsList } from "@/modules/plugins/installed-list";
import { listServerPlugins } from "@/modules/plugins/api";
import { getServer } from "@/modules/servers/api";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/shared/ui/card";
import { LinkButton } from "@/shared/ui/link-button";
import { Skeleton } from "@/shared/ui/skeleton";

export async function InstalledPluginsPanel({ serverId }: { serverId: number }) {
  const t = await getTranslations("plugins");
  const [result, server] = await Promise.all([
    listServerPlugins(serverId),
    getServer(serverId),
  ]);

  if (!result.ok) {
    return (
      <Card className="flex items-center gap-3 border-warn/30 bg-warn-muted/40 px-5 py-4 text-sm text-warn">
        <TriangleAlert className="size-4 shrink-0" />
        <span>{t("fetchError", { status: result.status || "network" })}</span>
      </Card>
    );
  }

  return (
    <div className="space-y-4">
    <Card>
      <CardHeader>
        <div>
          <CardTitle>{t("installedTitle")}</CardTitle>
          <CardDescription>{t("installedHelp")}</CardDescription>
        </div>
        <LinkButton
          href={`/plugins?serverId=${serverId}` as Route}
          variant="outline"
          size="sm"
        >
          {t("browseMarket")}
        </LinkButton>
      </CardHeader>
      <CardContent>
        {result.data.length === 0 ? (
          <div className="flex flex-wrap items-center gap-3">
            <p className="text-sm text-fg-muted">{t("installedEmpty")}</p>
            <LinkButton
              href={`/servers/${serverId}/frameworks` as Route}
              size="sm"
              variant="outline"
            >
              {t("goFrameworks")}
            </LinkButton>
          </div>
        ) : (
          <InstalledPluginsList serverId={serverId} plugins={result.data} />
        )}
      </CardContent>
    </Card>
    <GitHubInstallForm
      servers={[
        {
          id: serverId,
          name: server.ok ? server.data.name : `#${serverId}`,
          usePanelProxy: server.ok ? server.data.usePanelProxy : false,
          githubProxy: server.ok ? server.data.githubProxy : null,
        },
      ]}
      defaultServerId={serverId}
    />
    </div>
  );
}

export function InstalledPluginsPanelSkeleton() {
  return (
    <div className="rounded-lg border border-line bg-surface p-5 shadow-panel">
      <div className="mb-4 flex items-center gap-2">
        <Puzzle className="size-4 text-fg-subtle" />
        <Skeleton className="h-4 w-32" />
      </div>
      <Skeleton className="mb-2 h-8 w-full" />
      <Skeleton className="h-8 w-3/4" />
    </div>
  );
}