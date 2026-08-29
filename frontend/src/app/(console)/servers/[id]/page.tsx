import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { getTranslations } from "next-intl/server";
import { LinkButton } from "@/shared/ui/link-button";
import { PageHeader } from "@/shared/ui/page-header";
import { Card, CardContent, CardHeader, CardTitle } from "@/shared/ui/card";
import { Badge, StatusDot } from "@/shared/ui/badge";
import { ModulePlaceholder } from "@/shared/ui/module-placeholder";
import { getServer } from "@/modules/servers/api";
import { SERVER_STATUS_TONE } from "@/modules/servers/types";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("serverDetail");
  return { title: t("connection") };
}

export default async function ServerDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const serverId = Number(id);
  if (!Number.isInteger(serverId)) notFound();

  const [t, tServers] = await Promise.all([
    getTranslations("serverDetail"),
    getTranslations("servers"),
  ]);

  const result = await getServer(serverId);
  if (!result.ok && result.status === 404) notFound();

  const server = result.ok ? result.data : null;
  const tone = server ? SERVER_STATUS_TONE[server.status] : null;

  return (
    <>
      <PageHeader
        title={
          <span className="flex items-center gap-3">
            {server ? server.name : t("title", { id })}
            {server && tone ? (
              <Badge tone={tone}>
                <StatusDot tone={tone} pulse={server.status === "running"} />
                {tServers(`status.${server.status}`)}
              </Badge>
            ) : null}
          </span>
        }
        description={t("description")}
        actions={
          <LinkButton href="/servers" variant="outline">
            {tServers("backToList")}
          </LinkButton>
        }
      />

      {server ? (
        <Card className="mb-6 max-w-2xl">
          <CardHeader>
            <CardTitle>{t("connection")}</CardTitle>
          </CardHeader>
          <CardContent className="grid grid-cols-1 gap-x-8 gap-y-3 sm:grid-cols-2">
            <Field label={t("address")} value={`${server.host}:${server.gamePort}`} mono />
            <Field
              label={t("ssh")}
              value={`${server.sshUser}@${server.host}:${server.sshPort}`}
              mono
            />
            <Field label={t("defaultMap")} value={server.defaultMap} />
            <Field label={t("maxPlayers")} value={`${server.maxPlayers}`} />
            <Field
              label={t("gameMode")}
              value={`${server.gameMode} / ${server.gameType}`}
            />
            <Field label={t("directory")} value={server.gameDirectory} mono />
          </CardContent>
        </Card>
      ) : (
        <Card className="mb-6 border-warn/30 bg-warn-muted/40 px-5 py-4 text-sm text-warn">
          {t("fetchError", { status: result.ok ? "" : result.status || "network" })}
        </Card>
      )}

      <ModulePlaceholder phase={t("workspacePhase")}>
        {t("workspaceBody")}
      </ModulePlaceholder>
    </>
  );
}

function Field({
  label,
  value,
  mono = false,
}: {
  label: string;
  value: string;
  mono?: boolean;
}) {
  return (
    <div className="space-y-0.5">
      <p className="text-xs text-fg-subtle">{label}</p>
      <p className={mono ? "font-mono text-sm text-fg" : "text-sm text-fg"}>
        {value}
      </p>
    </div>
  );
}
