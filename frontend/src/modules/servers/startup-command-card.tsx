"use client";

import { useRef, useState } from "react";
import { useTranslations } from "next-intl";
import { confirmDeploymentAction } from "@/modules/servers/actions";
import { confirm as confirmDialog, notify } from "@/shared/feedback";
import { copyText, selectElementText } from "@/shared/lib/clipboard";
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
  const [copied, setCopied] = useState<"session" | "foreground" | null>(null);
  const [pending, setPending] = useState(false);
  const [banner, setBanner] = useState<string | null>(null);

  async function copy(
    kind: "session" | "foreground",
    value: string,
    el: HTMLElement | null,
  ) {
    const ok = await copyText(value);
    if (!ok) {
      selectElementText(el);
      setCopied(null);
      notify.error(t("copyFailed"));
      return;
    }
    setCopied(kind);
    notify.success(t("copied"));
    window.setTimeout(() => {
      setCopied((current) => (current === kind ? null : current));
    }, 1600);
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
      </CardHeader>
      <CardContent className="space-y-4">
        <CommandBlock
          label={t("sessionLabel")}
          value={command}
          copied={copied === "session"}
          copyLabel={copied === "session" ? t("copied") : t("copy")}
          onCopy={(el) => void copy("session", command, el)}
          testId="startup-command-session"
        />
        {cs2Command ? (
          <CommandBlock
            label={t("foregroundLabel")}
            help={t("foregroundHelp")}
            value={cs2Command}
            copied={copied === "foreground"}
            copyLabel={copied === "foreground" ? t("copied") : t("copy")}
            onCopy={(el) => void copy("foreground", cs2Command, el)}
            testId="startup-command-foreground"
          />
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

function CommandBlock({
  label,
  help,
  value,
  copied,
  copyLabel,
  onCopy,
  testId,
}: {
  label: string;
  help?: string;
  value: string;
  copied: boolean;
  copyLabel: string;
  onCopy: (el: HTMLElement | null) => void;
  testId: string;
}) {
  const preRef = useRef<HTMLPreElement>(null);
  return (
    <div className="space-y-2">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-xs font-medium text-fg">{label}</p>
          {help ? <p className="mt-0.5 text-xs text-fg-subtle">{help}</p> : null}
        </div>
        <Button
          type="button"
          size="sm"
          variant="outline"
          onClick={() => onCopy(preRef.current)}
        >
          {copyLabel}
        </Button>
      </div>
      <pre
        ref={preRef}
        data-testid={testId}
        data-copied={copied ? "true" : "false"}
        className="max-h-48 overflow-auto rounded-md border border-line bg-surface-raised p-3 font-mono text-xs text-fg"
      >
        {value}
      </pre>
    </div>
  );
}
