"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import type { Route } from "next";
import Link from "next/link";
import { Bot, Plus, TriangleAlert } from "lucide-react";
import {
  createAssistantConversationClient,
  decideAssistantToolClient,
  interruptAssistantConversationClient,
  loadAssistantConversationClient,
  loadAssistantWorkspace,
  sendAssistantMessageClient,
} from "@/modules/assistant/assistant-client";
import { AssistantMessages } from "@/modules/assistant/assistant-messages";
import {
  AssistantApprovals,
  AssistantStreamText,
  AssistantTokenActivity,
} from "@/modules/assistant/assistant-run-panel";
import { useAssistantRun } from "@/modules/assistant/use-assistant-run";
import {
  ASSISTANT_EXAMPLE_KEYS,
  type AssistantConversationDetail,
  type AssistantServerOption,
  type AssistantTool,
  type AssistantWorkspace,
} from "@/modules/assistant/types";
import { confirm } from "@/shared/feedback";
import { Badge } from "@/shared/ui/badge";
import { Button } from "@/shared/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/shared/ui/card";
import { Select } from "@/shared/ui/select";
import { Textarea } from "@/shared/ui/textarea";
import { cn } from "@/shared/lib/cn";

export function AssistantChat({
  initial,
  initialDetail,
  initialDraft = "",
  servers,
}: {
  initial: AssistantWorkspace;
  initialDetail: AssistantConversationDetail | null;
  initialDraft?: string;
  servers: readonly AssistantServerOption[];
}) {
  const t = useTranslations("assistant");
  const router = useRouter();
  const [workspace, setWorkspace] = useState(initial);
  const [detail, setDetail] = useState(initialDetail);
  const [draft, setDraft] = useState(initialDraft);
  const [selectedServerId, setSelectedServerId] = useState<number | null>(
    initialDetail?.serverId ?? null,
  );
  const [pending, setPending] = useState(false);
  const [runId, setRunId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const detailIdRef = useRef(initialDetail?.id ?? null);
  const listRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    detailIdRef.current = detail?.id ?? null;
  }, [detail?.id]);

  const serverById = useMemo(() => {
    const map = new Map<number, AssistantServerOption>();
    for (const server of servers) map.set(server.id, server);
    return map;
  }, [servers]);

  const selectedServer = selectedServerId == null ? null : (serverById.get(selectedServerId) ?? null);
  const boundServer = detail?.serverId == null ? null : (serverById.get(detail.serverId) ?? null);
  const busy = pending || Boolean(runId);

  const reloadConversation = useCallback(async () => {
    const id = detailIdRef.current;
    if (!id) return;
    const result = await loadAssistantConversationClient(id);
    if (result.ok && detailIdRef.current === id) setDetail(result.data);
  }, []);
  const finishRun = useCallback(() => {
    setRunId(null);
  }, []);
  const run = useAssistantRun(runId, reloadConversation, finishRun);

  useEffect(() => {
    listRef.current?.scrollTo({ top: listRef.current.scrollHeight });
  }, [detail, run.streamText, run.pendingTools, run.status]);

  async function refreshWorkspace() {
    const result = await loadAssistantWorkspace();
    if (result.ok) setWorkspace(result.data);
  }

  async function selectConversation(id: string) {
    const result = await loadAssistantConversationClient(id);
    if (!result.ok) {
      setError(result.error || t("failed"));
      return;
    }
    setDetail(result.data);
    detailIdRef.current = result.data.id;
    setSelectedServerId(result.data.serverId);
    setError(null);
    run.resetLive();
    setRunId(null);
    router.replace(`/assistant?conversation=${id}` as Route);
  }

  async function createConversation() {
    if (busy) return;
    setPending(true);
    setError(null);
    const result = await createAssistantConversationClient(selectedServerId);
    setPending(false);
    if (!result.ok) {
      setError(result.error || t("failed"));
      return;
    }
    await refreshWorkspace();
    await selectConversation(result.data.id);
  }

  async function confirmServer(): Promise<boolean> {
    if (selectedServerId == null) {
      return confirm({
        title: t("serverConfirmTitle"),
        description: t("serverConfirmNone"),
        tone: "default",
        confirmLabel: t("continueWithoutServer"),
      });
    }
    if (!selectedServer) {
      setError(t("serverMissing"));
      return false;
    }
    return confirm({
      title: t("serverConfirmTitle"),
      description: [
        t("serverConfirmIntro"),
        "",
        `${t("serverName")}: ${selectedServer.name}`,
        `${t("serverHost")}: ${selectedServer.host}`,
        `${t("serverSshUser")}: ${selectedServer.sshUser}`,
        `${t("serverGamePort")}: ${selectedServer.gamePort}`,
        `${t("serverId")}: ${selectedServer.id}`,
        "",
        t("serverConfirmWarning"),
      ].join("\n"),
      tone: "default",
      confirmLabel: t("confirmServer"),
    });
  }

  async function send() {
    if (!draft.trim() || busy) return;
    if (!(await confirmServer())) return;
    setPending(true);
    setError(null);
    run.setStatus(t("sending"));
    try {
      let conversationId = detail?.id ?? null;
      if (!conversationId || detail?.serverId !== selectedServerId) {
        const created = await createAssistantConversationClient(selectedServerId);
        if (!created.ok) {
          setError(created.error || t("failed"));
          return;
        }
        conversationId = created.data.id;
        detailIdRef.current = conversationId;
        await refreshWorkspace();
        router.replace(`/assistant?conversation=${conversationId}` as Route);
      }
      const content = draft.trim();
      const result = await sendAssistantMessageClient(conversationId, content);
      if (!result.ok) {
        setError(result.error || t("failed"));
        return;
      }
      setDraft("");
      run.resetLive();
      setRunId(result.data.id);
      run.setStatus(t("running"));
      const next = await loadAssistantConversationClient(conversationId);
      if (next.ok && detailIdRef.current === conversationId) setDetail(next.data);
    } finally {
      setPending(false);
    }
  }

  async function decide(tool: AssistantTool, decision: "approve" | "reject") {
    if (!runId) return;
    setPending(true);
    const result = await decideAssistantToolClient(runId, tool.id, decision, tool.argumentsHash);
    setPending(false);
    if (!result.ok) {
      setError(result.error || t("failed"));
      return;
    }
    run.dropTool(tool.id);
    run.setStatus(decision === "approve" ? t("approved") : t("rejected"));
  }

  const visibleError = error || run.error;

  return (
    <div className="grid gap-6 lg:grid-cols-[18rem_minmax(0,1fr)]">
      <Card>
        <CardHeader>
          <CardTitle>{t("conversations")}</CardTitle>
          <CardDescription>{t("conversationsHelp")}</CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <Button
            type="button"
            size="sm"
            disabled={busy || !workspace.providerReady}
            onClick={() => void createConversation()}
          >
            <Plus />
            {t("newConversation")}
          </Button>
          <ul className="space-y-1">
            {workspace.conversations.length === 0 ? (
              <li className="text-sm text-fg-muted">{t("empty")}</li>
            ) : (
              workspace.conversations.map((item) => {
                const bound = item.serverId == null ? null : serverById.get(item.serverId);
                return (
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
                      <span className="block truncate">{item.title}</span>
                      <span className="block truncate text-xs text-fg-subtle">
                        {bound ? bound.name : t("serverUnbound")}
                      </span>
                    </button>
                  </li>
                );
              })
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
            <CardContent className="flex flex-wrap gap-2">
              <Button asChild size="sm" variant="outline">
                <Link href={"/settings" as Route}>{t("openSettings")}</Link>
              </Button>
              <Button asChild size="sm" variant="ghost">
                <Link href={"/settings/profile" as Route}>{t("openProfile")}</Link>
              </Button>
            </CardContent>
          </Card>
        ) : (
          <div className="flex flex-wrap gap-2">
            <Badge tone="ok">{t("providerReady")}</Badge>
            {workspace.model ? <Badge>{workspace.model}</Badge> : null}
            <Badge tone="neutral">{workspace.mode}</Badge>
          </div>
        )}

        <Card>
          <CardHeader>
            <CardTitle>{t("serverContext")}</CardTitle>
            <CardDescription>{t("serverHelp")}</CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            <Select
              id="assistant-server"
              value={selectedServerId == null ? "" : String(selectedServerId)}
              disabled={busy}
              onChange={(event) => {
                const value = event.target.value;
                setSelectedServerId(value ? Number(value) : null);
              }}
            >
              <option value="">{t("noServer")}</option>
              {servers.map((server) => (
                <option key={server.id} value={server.id}>
                  {server.name} · {server.host}:{server.gamePort}
                </option>
              ))}
            </Select>
            {selectedServer ? (
              <p className="text-xs text-fg-subtle">
                {t("serverSummary", {
                  name: selectedServer.name,
                  host: selectedServer.host,
                  user: selectedServer.sshUser,
                  port: selectedServer.gamePort,
                })}
              </p>
            ) : (
              <p className="text-xs text-fg-subtle">{t("noServerHelp")}</p>
            )}
            {detail && detail.serverId !== selectedServerId ? (
              <p className="text-xs text-warn">{t("serverChangedHint")}</p>
            ) : null}
            {selectedServerId != null ? (
              <Button asChild size="sm" variant="ghost">
                <Link href={`/servers/${selectedServerId}/discord` as Route}>{t("openAgentPolicy")}</Link>
              </Button>
            ) : null}
          </CardContent>
        </Card>

        {visibleError ? (
          <p className="rounded-lg border border-danger/30 bg-danger-muted/40 px-4 py-3 text-sm text-danger">
            {visibleError}
          </p>
        ) : null}

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Bot className="size-4" />
              {detail?.title || t("title")}
            </CardTitle>
            <CardDescription>
              {boundServer
                ? t("boundServer", { name: boundServer.name })
                : detail
                  ? t("serverUnbound")
                  : t("help")}
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div
              ref={listRef}
              className="h-80 space-y-3 overflow-auto rounded-md border border-line bg-canvas p-3"
            >
              {!detail ? (
                <p className="text-sm text-fg-muted">{t("pickConversation")}</p>
              ) : detail.messages.length === 0 && !run.streamText ? (
                <p className="text-sm text-fg-muted">{t("noMessages")}</p>
              ) : (
                <AssistantMessages messages={detail.messages} />
              )}
              <AssistantStreamText streamText={run.streamText} />
            </div>

            <AssistantApprovals tools={run.pendingTools} pending={pending} onDecide={decide} />

            <AssistantTokenActivity busy={busy} status={run.status} tokenUsage={run.tokenUsage} />

            <div className="flex flex-wrap gap-1">
              {ASSISTANT_EXAMPLE_KEYS.map((key) => (
                <Button
                  key={key}
                  type="button"
                  size="sm"
                  variant="ghost"
                  disabled={busy || !workspace.providerReady}
                  onClick={() => setDraft(t(`examplePrompts.${key}`))}
                >
                  {t(`exampleLabels.${key}`)}
                </Button>
              ))}
            </div>

            <Textarea
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              disabled={!workspace.providerReady || busy}
              rows={3}
              placeholder={t("placeholder")}
            />
            <div className="flex flex-wrap gap-2">
              <Button
                type="button"
                disabled={!workspace.providerReady || busy || !draft.trim()}
                onClick={() => void send()}
              >
                {pending ? t("sending") : t("send")}
              </Button>
              <Button
                type="button"
                variant="outline"
                disabled={!detail || !runId}
                onClick={() => {
                  if (detail) void interruptAssistantConversationClient(detail.id);
                  setRunId(null);
                  run.resetLive();
                  run.setStatus(t("interrupt"));
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
