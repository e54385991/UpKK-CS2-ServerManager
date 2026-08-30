"use client";

import { useState } from "react";
import { useTranslations } from "next-intl";
import { CircleStop, LoaderCircle } from "lucide-react";
import { clearDeploymentLockFromBrowser } from "@/modules/servers/operation-client";
import { confirm, notify } from "@/shared/feedback";
import { Button } from "@/shared/ui/button";

export function ForceStopButton({
  serverId,
  onDone,
}: {
  serverId: number;
  onDone?: () => void | Promise<void>;
}) {
  const t = useTranslations("serverDetail");
  const [pending, setPending] = useState(false);

  async function onForceStop() {
    if (!(await confirm(t("confirm.forceStop")))) return;
    setPending(true);
    const result = await clearDeploymentLockFromBrowser(serverId);
    setPending(false);
    if (!result.ok) {
      notify.error(result.error || t("forceStopFail"));
      return;
    }
    if (onDone) await onDone();
  }

  return (
    <Button
      type="button"
      size="sm"
      variant="danger"
      disabled={pending}
      onClick={() => void onForceStop()}
    >
      {pending ? <LoaderCircle className="animate-spin" /> : <CircleStop />}
      {pending ? t("forceStopping") : t("forceStop")}
    </Button>
  );
}
