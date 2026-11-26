"use client";

import { useState } from "react";
import { useRouter } from "@/i18n/navigation";
import { useTranslations } from "next-intl";
import { LoaderCircle, RefreshCw } from "lucide-react";
import { reconnectServerSshAction } from "@/modules/servers/actions";
import { SshHealthDetails, sshHealthTone, type SshHealthFields } from "./ssh-health-details";
import { Button } from "@/shared/ui/button";

export function SshHealthBlock({ server }: { server: SshHealthFields }) {
  const t = useTranslations("servers.sshHealth");
  const router = useRouter();
  const [pending, setPending] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);

  const tone = sshHealthTone(server.sshHealthStatus, server.isSshDown);
  const canReconnect =
    server.isSshDown ||
    server.sshHealthStatus === "completely_down" ||
    server.sshHealthStatus === "down";

  async function onReconnect() {
    setPending(true);
    setMessage(null);
    setFailed(false);
    const result = await reconnectServerSshAction(server.id);
    setPending(false);
    if (!result.ok) {
      setFailed(true);
      setMessage(result.error || t("reconnectFail"));
      return;
    }
    setFailed(!result.data.success);
    setMessage(
      result.data.message ||
        (result.data.success ? t("reconnectOk") : t("reconnectFail")),
    );
    router.refresh();
  }

  return (
    <SshHealthDetails server={server}>
      {canReconnect ? (
        <Button
          type="button"
          size="sm"
          variant={tone === "danger" ? "primary" : "outline"}
          disabled={pending}
          onClick={() => void onReconnect()}
        >
          {pending ? <LoaderCircle className="animate-spin" /> : <RefreshCw />}
          {pending ? t("reconnecting") : t("reconnect")}
        </Button>
      ) : null}
      {message ? (
        <p className={failed ? "text-xs text-danger" : "text-xs text-ok"} role="status">
          {message}
        </p>
      ) : null}
    </SshHealthDetails>
  );
}
