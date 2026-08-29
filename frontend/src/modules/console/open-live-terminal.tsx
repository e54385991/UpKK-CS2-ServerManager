"use client";

import { useTranslations } from "next-intl";
import { Rocket, SquareTerminal } from "lucide-react";
import {
  liveConsoleHref,
  type LiveTerminalView,
} from "@/modules/console/live-console";
import { Button } from "@/shared/ui/button";

export type { LiveTerminalView };

function popupFeatures(): string {
  const width = Math.min(1100, window.screen.availWidth - 40);
  const height = Math.min(800, window.screen.availHeight - 40);
  const left = Math.max(0, Math.round((window.screen.availWidth - width) / 2));
  const top = Math.max(0, Math.round((window.screen.availHeight - height) / 2));
  return `popup=yes,width=${width},height=${height},left=${left},top=${top},menubar=no,toolbar=no,location=no,status=no`;
}

export function openLiveTerminal(serverId: number, view: LiveTerminalView) {
  window.open(
    liveConsoleHref(serverId, view),
    `upkk-live-console-${serverId}-${view}`,
    popupFeatures(),
  );
}

export function OpenLiveTerminalButton({
  serverId,
  view,
  testId,
  label,
}: {
  serverId: number;
  view: LiveTerminalView;
  testId?: string;
  label?: string;
}) {
  const t = useTranslations("console");
  const text =
    label ??
    (view === "ssh"
      ? t("openSsh")
      : view === "game"
        ? t("openGame")
        : t("openDeploy"));
  return (
    <Button
      type="button"
      size="sm"
      variant="outline"
      data-testid={testId ?? `open-live-${view}`}
      onClick={() => openLiveTerminal(serverId, view)}
    >
      {view === "deploy" ? <Rocket /> : <SquareTerminal />}
      {text}
    </Button>
  );
}

export function LiveConsolePopups({
  serverId,
  className,
}: {
  serverId: number;
  className?: string;
}) {
  return (
    <div className={className ?? "flex flex-wrap items-center gap-2"}>
      <OpenLiveTerminalButton serverId={serverId} view="ssh" />
      <OpenLiveTerminalButton serverId={serverId} view="game" />
      <OpenLiveTerminalButton serverId={serverId} view="deploy" />
    </div>
  );
}
