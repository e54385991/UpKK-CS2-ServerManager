"use client";

import { useEffect, useRef } from "react";
import { useTranslations } from "next-intl";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/shared/ui/card";

export function SetupLiveLog({
  logs,
  pending,
}: {
  logs: readonly string[];
  pending: boolean;
}) {
  const t = useTranslations("setupWizard");
  const scroller = useRef<HTMLPreElement>(null);

  useEffect(() => {
    const node = scroller.current;
    if (!node) return;
    node.scrollTop = node.scrollHeight;
  }, [logs]);

  if (!pending && logs.length === 0) return null;

  return (
    <Card data-testid="setup-live-log">
      <CardHeader>
        <div>
          <CardTitle>{t("liveLogTitle")}</CardTitle>
          <CardDescription>
            {pending ? t("liveLogHelp") : t("liveLogDone")}
          </CardDescription>
        </div>
      </CardHeader>
      <CardContent>
        <pre
          ref={scroller}
          className="log-terminal max-h-80 overflow-auto rounded-md border border-line bg-canvas px-3 py-2 font-mono text-xs leading-5 text-fg"
        >
          {logs.length > 0 ? logs.join("\n") : t("liveLogWaiting")}
        </pre>
      </CardContent>
    </Card>
  );
}
