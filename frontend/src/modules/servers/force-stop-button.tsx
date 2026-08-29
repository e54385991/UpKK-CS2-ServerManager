"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { CircleStop, LoaderCircle } from "lucide-react";
import { clearDeploymentLockAction } from "@/modules/servers/actions";
import { Button } from "@/shared/ui/button";

export function ForceStopButton({
  serverId,
  onDone,
}: {
  serverId: number;
  onDone?: () => void | Promise<void>;
}) {
  const t = useTranslations("serverDetail");
  const router = useRouter();
  const [pending, setPending] = useState(false);

  async function onForceStop() {
    if (!window.confirm(t("confirm.forceStop"))) return;
    setPending(true);
    const result = await clearDeploymentLockAction(serverId);
    setPending(false);
    if (!result.ok) {
      window.alert(result.error || t("forceStopFail"));
      return;
    }
    if (onDone) await onDone();
    router.refresh();
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
