"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import type { Route } from "next";
import { Bot, Plus, TriangleAlert } from "lucide-react";
import {
  createConversationAction,
  interruptConversationAction,
  loadConversationAction,
  sendMessageAction,
} from "@/modules/assistant/actions";
import type {
  AssistantConversationDetail,
  AssistantWorkspace,
} from "@/modules/assistant/types";
import { Badge } from "@/shared/ui/badge";
import { Button } from "@/shared/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/shared/ui/card";
import { Textarea } from "@/shared/ui/textarea";
import { cn } from "@/shared/lib/cn";

export function AssistantChat({
  initial,
  initialDetail,
}: {
  initial: AssistantWorkspace;
  initialDetail: AssistantConversationDetail | null;
}) {
  const t = useTranslations("assistant");
  const router = useRouter();
  const [workspace, setWorkspace] = useState(initial);
  const [detail, setDetail] = useState(initialDetail);
  const [draft, setDraft] = useState("");
  const [pending, setPending] = useState(false);
  const [runId, setRunId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function selectConversation(id: string) {
    const result = await loadConversationAction(id);
    if (!result.ok) {
      setError(result.error || t("failed"));
      return;
    }
    setDetail(result.data);
    router.replace(`/assistant?conversation=${id}` as Route);
  }

  async function createConversation() {
    setPending(true);
    setError(null);
    const result = await createConversationAction();
    setPending(false);
    if (!result.ok) {
      setError(result.error || t("failed"));
      return;
    }
    setWorkspace((current) => ({
      ...current,
      conversations: [result.data, ...current.conversations],
    }));
    await selectConversation(result.data.id);
  }

  async function send() {
    if (!detail || !draft.trim() || pending) return;
    setPending(true);
    setError(null);
    const result = await sendMessageAction(detail.id, draft.trim());
    setPending(false);
    if (!result.ok) {
      setError(result.error || t("failed"));
      return;
    }
    setDraft("");
    setRunId(result.data.id);
    const next = await loadConversationAction(detail.id);
    if (next.ok) setDetail(next.data);
  }

  useEffect(() => {
    if (!runId || !detail) return;
    const source = new EventSource(`/ai-stream/runs/${runId}`);
    source.addEventListener("message", () => {
      void loadConversationAction(detail.id).then((result) => {
        if (result.ok) setDetail(result.data);
      });
    });
    source.addEventListener("run_completed", () => {
      setRunId(null);
      source.close();
    });
    source.addEventListener("run_failed", () => {
      setRunId(null);
      source.close();
    });
    source.addEventListener("run_interrupted", () => {
      setRunId(null);
      source.close();
    });
    return () => source.close();
  }, [detail, runId]);

  return (
    <div className="grid gap-6 lg:grid-cols-[18rem_minmax(0,1fr)]">
      <Card>
        <CardHeader>
          <CardTitle>{t("conversations")}</CardTitle>
          <CardDescription>{t("conversationsHelp")}</CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <Button type="button" size="sm" disabled={pending || !workspace.providerReady} onClick={() => void createConversation()}>
            <Plus />
            {t("newConversation")}
          </Button>
          <ul className="space-y-1">
            {workspace.conversations.length === 0 ? (
              <li className="text-sm text-fg-muted">{t("empty")}</li>
            ) : (
              workspace.conversations.map((item) => (
                <li key={item.id}>
                  <button
                    type="button"
                    onClick={() => void selectConversation(item.id)}
                    className={cn(
                      "w-full rounded-md px-3 py-2 text-left text-sm",
                      detail?.id === item.id
                        ? "bg-primary-muted text-fg"
                        : "text-fg-muted hover:bg-surface-overlay",
                    )}
                  >
                    {item.title}
                  </button>
                </li>
              ))
            )}
          </ul>
        </CardContent>
      </Card>

      <div className="space-y-4">
        {!workspace.providerReady ? (
          <Card className="border-warn/30 bg-warn-muted/20">
            <CardHeader>
              <CardTitle className="flex items-center gap-2 text-warn">
                <TriangleAlert className="size-4" />
                {t("providerOff")}
              </CardTitle>
              <CardDescription>{t("providerOffHelp")}</CardDescription>
            </CardHeader>
          </Card>
        ) : (
          <div className="flex flex-wrap gap-2">
            <Badge tone="ok">{t("providerReady")}</Badge>
            {workspace.model ? <Badge>{workspace.model}</Badge> : null}
            <Badge tone="neutral">{workspace.mode}</Badge>
          </div>
        )}

        {error ? (
          <p className="rounded-lg border border-danger/30 bg-danger-muted/40 px-4 py-3 text-sm text-danger">
            {error}
          </p>
        ) : null}

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Bot className="size-4" />
              {detail?.title || t("title")}
            </CardTitle>
            <CardDescription>{t("help")}</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="h-80 space-y-3 overflow-auto rounded-md border border-line bg-canvas p-3">
              {!detail ? (
                <p className="text-sm text-fg-muted">{t("pickConversation")}</p>
              ) : detail.messages.length === 0 ? (
                <p className="text-sm text-fg-muted">{t("noMessages")}</p>
              ) : (
                detail.messages.map((message) => (
                  <div key={message.id} className="space-y-1">
                    <p className="text-xs font-medium text-fg-subtle">{message.role}</p>
                    <p className="whitespace-pre-wrap text-sm text-fg">
                      {message.content || message.toolName || "—"}
                    </p>
                  </div>
                ))
              )}
            </div>
            <Textarea
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              disabled={!detail || !workspace.providerReady || pending}
              rows={3}
              placeholder={t("placeholder")}
            />
            <div className="flex flex-wrap gap-2">
              <Button
                type="button"
                disabled={!detail || !workspace.providerReady || pending || !draft.trim()}
                onClick={() => void send()}
              >
                {pending ? t("sending") : t("send")}
              </Button>
              <Button
                type="button"
                variant="outline"
                disabled={!detail || !runId}
                onClick={() => {
                  if (detail) void interruptConversationAction(detail.id);
                  setRunId(null);
                }}
              >
                {t("interrupt")}
              </Button>
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
