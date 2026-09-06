import { getFormatter, getTranslations } from "next-intl/server";
import type { Route } from "next";
import {
  getServer,
  getServerDiskSpace,
  getStartupCommand,
} from "@/modules/servers/api";
import { DiskSpaceCard } from "@/modules/servers/disk-space-card";
import { StartupCommandCard } from "@/modules/servers/startup-command-card";
import { workspaceHref } from "@/modules/servers/workspace";
import { Card, CardContent, CardHeader, CardTitle } from "@/shared/ui/card";
import { LinkButton } from "@/shared/ui/link-button";

export async function OverviewPanel({ serverId }: { serverId: number }) {
  // The disk read is deliberately cache-only (no `force_refresh`): `du` over the
  // game tree is expensive, so opening the overview must never trigger it.
  const [t, tStartup, format, result, startup, disk] = await Promise.all([
    getTranslations("serverDetail"),
    getTranslations("startupCommand"),
    getFormatter(),
    getServer(serverId),
    getStartupCommand(serverId),
    getServerDiskSpace(serverId),
  ]);

  if (!result.ok) {
    return (
      <Card className="border-warn/30 bg-warn-muted/40 px-5 py-4 text-sm text-warn">
        {t("fetchError", { status: result.status || "network" })}
      </Card>
    );
  }

  const server = result.data;
  const undeployed = server.status === "pending";

  return (
    <div className="space-y-6">
      <Card className="max-w-3xl">
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
          {server.sshUser.trim().toLowerCase() === "root" ? (
            <p className="sm:col-span-2 text-sm text-warn">{t("rootSshUserWarning")}</p>
          ) : null}
          <Field
            label={t("sshPool")}
            value={
              server.sshPooled
                ? server.sshInUse
                  ? t("sshPoolBusy", { leases: server.sshActiveLeases })
                  : t("sshPoolIdle", {
                      seconds: Math.max(0, Math.round(server.sshIdleSeconds ?? 0)),
                    })
                : t("sshPoolNone")
            }
          />
          <Field label={t("defaultMap")} value={server.defaultMap} />
          <Field label={t("maxPlayers")} value={`${server.maxPlayers}`} />
          <Field
            label={t("gameMode")}
            value={`${server.gameMode} / ${server.gameType}`}
          />
          <Field label={t("directory")} value={server.gameDirectory} mono />
          <Field
            label={t("created")}
            value={formatStamp(server.createdAt, format.dateTime)}
          />
          <Field
            label={t("lastDeployed")}
            value={
              server.lastDeployed
                ? formatStamp(server.lastDeployed, format.dateTime)
                : t("neverDeployed")
            }
          />
          <Field
            label={t("descriptionLabel")}
            value={server.description || t("noDescription")}
          />
        </CardContent>
      </Card>

      <DiskSpaceCard
        serverId={server.id}
        gameDirectory={server.gameDirectory}
        disk={disk.ok ? disk.data : null}
      />

      {undeployed ? (
        <Card className="max-w-3xl border-warn/30 bg-warn-muted/30 px-5 py-4">
          <p className="text-sm font-medium text-warn">{t("notDeployed")}</p>
          <p className="mt-1 text-sm text-fg-muted">{t("notDeployedHelp")}</p>
          <div className="mt-3">
            <LinkButton
              href={workspaceHref(server.id, "operations")}
              size="sm"
            >
              {t("goDeploy")}
            </LinkButton>
          </div>
        </Card>
      ) : null}

      {startup.ok ? (
        <StartupCommandCard
          serverId={server.id}
          command={startup.data.startupCommand}
          cs2Command={startup.data.cs2Command}
          undeployed={undeployed}
        />
      ) : (
        <Card className="max-w-3xl border-warn/30 bg-warn-muted/40 px-5 py-4 text-sm text-warn">
          {tStartup("fetchError", { status: startup.status || "network" })}
        </Card>
      )}

      <div className="flex flex-wrap gap-2">
        <LinkButton
          href={workspaceHref(server.id, "operations") as Route}
          variant="outline"
          size="sm"
        >
          {t("goOperations")}
        </LinkButton>
        <LinkButton
          href={workspaceHref(server.id, "config")}
          variant="outline"
          size="sm"
        >
          {t("goConfig")}
        </LinkButton>
        <LinkButton
          href={workspaceHref(server.id, "host-config")}
          variant="outline"
          size="sm"
        >
          {t("goHostConfig")}
        </LinkButton>
        <LinkButton
          href={workspaceHref(server.id, "frameworks")}
          variant="outline"
          size="sm"
        >
          {t("goFrameworks")}
        </LinkButton>
        <LinkButton
          href={workspaceHref(server.id, "game-modes")}
          variant="outline"
          size="sm"
        >
          {t("goGameModes")}
        </LinkButton>
        <LinkButton
          href={workspaceHref(server.id, "plugins")}
          variant="outline"
          size="sm"
        >
          {t("goPlugins")}
        </LinkButton>
      </div>
    </div>
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

type DateTimeFormatter = Awaited<ReturnType<typeof getFormatter>>["dateTime"];

function formatStamp(value: string, formatDateTime: DateTimeFormatter): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return formatDateTime(date, { dateStyle: "medium", timeStyle: "medium" });
}
