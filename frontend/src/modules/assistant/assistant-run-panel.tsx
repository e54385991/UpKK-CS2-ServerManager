"use client";

import { useFormatter, useTranslations } from "next-intl";
import { LoaderCircle } from "lucide-react";
import type { TokenUsage } from "@/modules/assistant/assistant-tokens";
import type { AssistantTool } from "@/modules/assistant/types";
import { Button } from "@/shared/ui/button";
import { cn } from "@/shared/lib/cn";

export function AssistantStreamText({ streamText }: { streamText: string }) {
  const t = useTranslations("assistant");
  if (!streamText) return null;
  return (
    <div className="space-y-1">
      <p className="text-xs font-medium text-fg-subtle">{t("roleAssistant")}</p>
      <p className="whitespace-pre-wrap text-sm text-fg">{streamText}</p>
    </div>
  );
}

export function AssistantApprovals({
  tools,
  pending,
  onDecide,
}: {
  tools: readonly AssistantTool[];
  pending: boolean;
  onDecide: (tool: AssistantTool, decision: "approve" | "reject") => void;
}) {
  const t = useTranslations("assistant");
  if (tools.length === 0) return null;
  return (
    <div className="space-y-3">
      {tools.map((tool) => (
        <div key={tool.id} className="space-y-2 rounded-md border border-warn/40 bg-warn-muted/20 p-3">
          <p className="text-sm font-medium text-fg">
            {t("approvalRequired")}: {tool.toolName}
          </p>
          <pre className="max-h-40 overflow-auto rounded-md bg-canvas p-2 text-xs text-fg-muted">
            {JSON.stringify({ summary: tool.summary, arguments: tool.arguments }, null, 2)}
          </pre>
          <div className="flex flex-wrap gap-2">
            <Button type="button" size="sm" disabled={pending} onClick={() => onDecide(tool, "approve")}>
              {t("approve")}
            </Button>
            <Button
              type="button"
              size="sm"
              variant="outline"
              disabled={pending}
              onClick={() => onDecide(tool, "reject")}
            >
              {t("reject")}
            </Button>
          </div>
        </div>
      ))}
    </div>
  );
}

export function AssistantTokenActivity({
  busy,
  status,
  tokenUsage,
}: {
  busy: boolean;
  status: string | null;
  tokenUsage: TokenUsage;
}) {
  const t = useTranslations("assistant");
  const format = useFormatter();
  if (busy || tokenUsage.total > 0) {
    return (
      <div
        className="rounded-md border border-primary/30 bg-primary-muted/20 px-3 py-2 text-xs text-fg-muted"
        aria-live="polite"
        data-testid="assistant-token-activity"
      >
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
          {busy ? <LoaderCircle className="size-3.5 animate-spin text-primary" /> : null}
          <span className={cn(busy && "font-medium text-fg")}>{status || t("thinking")}</span>
          <span>{t("inputTokens", { count: format.number(tokenUsage.input) })}</span>
          <span>{t("outputTokens", { count: format.number(tokenUsage.output) })}</span>
          <span>{t("totalTokens", { count: format.number(tokenUsage.total) })}</span>
          {tokenUsage.cached > 0 ? (
            <span data-testid="assistant-cached-tokens">
              {t("cachedTokens", { count: format.number(tokenUsage.cached) })}
            </span>
          ) : null}
          {tokenUsage.estimated ? <span className="text-fg-subtle">{t("estimated")}</span> : null}
        </div>
        {busy ? <p className="mt-1 text-fg-subtle">{t("thinkingHint")}</p> : null}
      </div>
    );
  }
  if (status) {
    return <p className="text-xs text-fg-subtle">{status}</p>;
  }
  return null;
}
