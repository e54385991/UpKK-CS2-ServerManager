"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { RefreshCw, TriangleAlert } from "lucide-react";
import { refreshConsoleAction } from "@/modules/console/actions";
import { ConsoleTerminal } from "@/modules/console/console-terminal";
import { LiveConsolePopups } from "@/modules/console/open-live-terminal";
import { useConsolePane } from "@/modules/console/use-console-pane";
import type { ConsoleKind, ConsolePane, ConsoleWorkspace } from "@/modules/console/types";
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

type Banner = { readonly tone: "ok" | "warn" | "danger"; readonly text: string };

function WorkspaceStatus({
  workspace,
}: {
  workspace: ConsoleWorkspace;
}) {
  const t = useTranslations("console");
  return (
    <div className="flex flex-wrap gap-2">
      <Badge tone={workspace.sshOk ? "ok" : "danger"}>
        {workspace.sshOk ? t("sshUp") : t("sshDown")}
      </Badge>
      <Badge tone={workspace.gameRunning ? "ok" : "neutral"}>
        {workspace.gameRunning ? t("gameUp") : t("gameDown")}
      </Badge>
      <Badge tone={workspace.steamcmdRunning ? "ok" : "neutral"}>
        {workspace.steamcmdRunning ? t("steamcmdUp") : t("steamcmdDown")}
      </Badge>
      <Badge>{workspace.sessionManager}</Badge>
      <Badge tone="neutral">{workspace.host}</Badge>
    </div>
  );
}

export function ConsoleLauncherView({
  initial,
}: {
  initial: ConsoleWorkspace;
}) {
  const t = useTranslations("console");
  const router = useRouter();
  const [workspace, setWorkspace] = useState(initial);
  const [pending, setPending] = useState(false);
  const [banner, setBanner] = useState<Banner | null>(
    initial.message ? { tone: "ok", text: initial.message } : null,
  );

  async function refresh() {
    setPending(true);
    const result = await refreshConsoleAction(workspace.serverId);
    setPending(false);
    if (!result.ok) {
      setBanner({ tone: "danger", text: result.error || t("failed") });
      return;
    }
    setWorkspace(result.data);
    setBanner(result.data.message ? { tone: "ok", text: result.data.message } : null);
    router.refresh();
  }

  return (
    <div className="space-y-6">
      {banner ? (
        <p
          className={cn(
            "rounded-lg border px-4 py-3 text-sm",
            banner.tone === "ok" && "border-ok/30 bg-ok-muted/40 text-ok",
            banner.tone === "warn" && "border-warn/30 bg-warn-muted/40 text-warn",
            banner.tone === "danger" && "border-danger/30 bg-danger-muted/40 text-danger",
          )}
        >
          {banner.text}
        </p>
      ) : null}

      {!workspace.sshOk ? (
        <Card className="border-danger/30 bg-danger-muted/20">
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-danger">
              <TriangleAlert className="size-4" />
              {t("sshDown")}
            </CardTitle>
            <CardDescription>{workspace.sshError || t("sshDownHelp")}</CardDescription>
          </CardHeader>
        </Card>
      ) : null}

      <div className="flex flex-wrap items-center justify-between gap-3">
        <WorkspaceStatus workspace={workspace} />
        <Button type="button" variant="outline" size="sm" disabled={pending} onClick={() => void refresh()}>
          <RefreshCw />
          {t("refresh")}
        </Button>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>{t("launcherTitle")}</CardTitle>
          <CardDescription>{t("launcherHelp")}</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <LiveConsolePopups serverId={workspace.serverId} />
          <p className="text-xs text-fg-subtle">{t("popupHint")}</p>
        </CardContent>
      </Card>
    </div>
  );
}

export function ConsoleWorkspaceView({
  initial,
}: {
  initial: ConsoleWorkspace;
}) {
  return <ConsoleLauncherView initial={initial} />;
}

export function FocusedConsoleView({
  initial,
  kind,
  seedPane = null,
}: {
  initial: ConsoleWorkspace;
  kind: ConsoleKind;
  seedPane?: ConsolePane | null;
}) {
  const t = useTranslations("console");
  const livePane = useConsolePane({
    serverId: initial.serverId,
    kind: "game",
    initial: seedPane,
    enabled: kind === "game",
  });
  const disabled =
    !initial.sshOk || (kind === "game" && !initial.gameRunning && !livePane?.running);
  const hint =
    !initial.sshOk
      ? t("listLocked")
      : kind === "game"
        ? initial.gameRunning || livePane?.running
          ? t("gameHint")
          : t("gameNotRunning")
        : t("sshHint");

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-sm font-medium text-fg">
            {kind === "game" ? t("gameTitle") : t("sshTitle")}
          </h2>
          <p className="text-xs text-fg-muted">
            {kind === "game" ? t("gameHelp") : t("sshHelp")}
          </p>
        </div>
        <WorkspaceStatus workspace={initial} />
      </div>
      <ConsoleTerminal
        serverId={initial.serverId}
        kind={kind}
        disabled={disabled}
        hint={hint}
        autoConnect
        seedPane={kind === "game" ? livePane : null}
      />
    </div>
  );
}
