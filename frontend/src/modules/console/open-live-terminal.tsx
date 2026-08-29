"use client";

import { useTranslations } from "next-intl";
import { SquareTerminal } from "lucide-react";
import {
  liveConsoleHref,
  type LiveConsolePreferredView,
} from "@/modules/console/live-console";
import { Button } from "@/shared/ui/button";

export type LiveTerminalView = Exclude<LiveConsolePreferredView, "auto">;

export function openLiveTerminal(serverId: number, view?: LiveTerminalView) {
  const width = Math.min(1100, window.screen.availWidth - 40);
  const height = Math.min(760, window.screen.availHeight - 40);
  const left = Math.max(0, Math.round((window.screen.availWidth - width) / 2));
  const top = Math.max(0, Math.round((window.screen.availHeight - height) / 2));
  window.open(
    liveConsoleHref(serverId, view),
    `upkk-live-console-${serverId}`,
    `popup=yes,width=${width},height=${height},left=${left},top=${top},menubar=no,toolbar=no,location=no,status=no`,
  );
}

export function OpenLiveTerminalButton({
  serverId,
  view,
}: {
  serverId: number;
  view?: LiveTerminalView;
}) {
  const t = useTranslations("serverDetail");
  return (
    <Button
      type="button"
      size="sm"
      variant="outline"
      onClick={() => openLiveTerminal(serverId, view)}
    >
      <SquareTerminal />
      {t("openLiveTerminal")}
    </Button>
  );
}
