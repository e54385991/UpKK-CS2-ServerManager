"use client";

import { useState } from "react";
import { useTranslations } from "next-intl";
import { confirmDeploymentAction } from "@/modules/servers/actions";
import { confirm as confirmDialog } from "@/shared/feedback";
import { Button } from "@/shared/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/shared/ui/card";

export function StartupCommandCard({
  serverId,
  command,
  cs2Command,
  undeployed,
}: {
  serverId: number;
  command: string;
  cs2Command: string;
  undeployed: boolean;
}) {
  const t = useTranslations("startupCommand");
  const [copied, setCopied] = useState(false);
  const [pending, setPending] = useState(false);
  const [banner, setBanner] = useState<string | null>(null);

  async function copy() {
    try {
      await navigator.clipboard.writeText(command);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  }

  async function confirm() {
    if (!(await confirmDialog(t("confirmPrompt")))) return;
    setPending(true);
    setBanner(null);
    const result = await confirmDeploymentAction(serverId);
    setPending(false);
    if (!result.ok) {
      setBanner(result.error || t("confirmFailed"));
      return;
    }
    setBanner(result.data.message);
  }

  return (
    <Card className="max-w-3xl">
      <CardHeader>
        <div>
          <CardTitle>{t("title")}</CardTitle>
          <CardDescription>{t("help")}</CardDescription>
        </div>
        <Button type="button" size="sm" variant="outline" onClick={() => void copy()}>
          {copied ? t("copied") : t("copy")}
        </Button>
      </CardHeader>
      <CardContent className="space-y-3">
        <pre className="max-h-48 overflow-auto rounded-md border border-line bg-surface-raised p-3 font-mono text-xs text-fg">
          {command}
        </pre>
        {cs2Command ? (
          <p className="font-mono text-xs text-fg-muted">{cs2Command}</p>
        ) : null}
        {undeployed ? (
          <div className="space-y-2">
            <p className="text-sm text-fg-muted">{t("confirmHelp")}</p>
            <Button
              type="button"
              size="sm"
              variant="outline"
              disabled={pending}
              onClick={() => void confirm()}
            >
              {pending ? t("confirming") : t("confirm")}
            </Button>
          </div>
        ) : null}
        {banner ? (
          <p className="text-sm text-fg-muted" role="status">
            {banner}
          </p>
        ) : null}
      </CardContent>
    </Card>
  );
}
