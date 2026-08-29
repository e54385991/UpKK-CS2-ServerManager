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
  loadAssistantRunClient,
  loadAssistantWorkspace,
  sendAssistantMessageClient,
} from "@/modules/assistant/assistant-client";
import {
  parseAssistantSseData,
  toolFromApprovalPayload,
} from "@/modules/assistant/assistant-wire";
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

const TERMINAL_RUN = new Set(["completed", "failed", "interrupted", "expired", "cancelled"]);

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
  const [status, setStatus] = useState<string | null>(null);
  const [streamText, setStreamText] = useState("");
  const [pendingTools, setPendingTools] = useState<AssistantTool[]>([]);
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

  const selectedServer = selectedServerId == null ? null : serverById.get(selectedServerId) ?? null;
  const boundServer = detail?.serverId == null ? null : serverById.get(detail.serverId) ?? null;
  const busy = pending || Boolean(runId);

  const reloadConversation = useCallback(async () => {
    const id = detailIdRef.current;
    if (!id) return;
    const result = await loadAssistantConversationClient(id);
    if (result.ok) setDetail(result.data);
  }, []);

  useEffect(() => {
    listRef.current?.scrollTo({ top: listRef.current.scrollHeight });
  }, [detail, streamText, pendingTools, status]);

  useEffect(() => {
    if (!runId) return;
    let closed = false;
    const source = new EventSource(`/ai-stream/runs/${runId}`);

    function onEvent(raw: MessageEvent<string>) {
      const event = parseAssistantSseData(raw.data);
      if (!event) return;
      if (event.type === "assistant_delta") {
        const delta = typeof event.payload.delta === "string" ? event.payload.delta : "";
        if (delta) setStreamText((current) => current + delta);
        return;
      }
      if (event.type === "assistant_message") {
        setStreamText("");
        void reloadConversation();
        return;
      }
      if (event.type === "tool_approval_required") {
        const tool = toolFromApprovalPayload(event.payload);
        if (tool) {
          setPendingTools((current) =>
            current.some((item) => item.id === tool.id) ? current : [...current, tool],
          );
        }
        setStatus(t("waitingApproval"));
        return;
      }
      if (event.type === "run_waiting_approval") {
        setStatus(t("waitingApproval"));
        return;
      }
      if (event.type === "tool_started" || event.type === "tool_queued") {
        const name = typeof event.payload.tool_name === "string" ? event.payload.tool_name : "";
        setStatus(name ? t("runningTool", { name }) : t("running"));
        return;
      }
      if (event.type === "tool_progress" || event.type === "diagnostic_progress") {
        if (typeof event.payload.message === "string" && event.payload.message) {
          setStatus(event.payload.message);
        }
        return;
      }
      if (event.type === "run_completed") {
        finishRun(false, t("completed"));
        return;
      }
      if (event.type === "run_failed" || event.type === "run_interrupted") {
        const message =
          typeof event.payload.error === "string" && event.payload.error
            ? event.payload.error
            : t("runFailed");
        finishRun(true, message);
      }
    }

    function finishRun(failed: boolean, message: string) {
      if (closed) return;
      closed = true;
      setRunId(null);
      setStreamText("");
      setPendingTools([]);
      setStatus(message);
      if (failed) setError(message);
      void reloadConversation();
      source.close();
    }

    const named = [
      "assistant_delta",
      "assistant_message",
      "tool_approval_required",
      "run_waiting_approval",
      "tool_started",
      "tool_queued",
      "tool_progress",
      "diagnostic_progress",
      "run_completed",
      "run_failed",
      "run_interrupted",
    ];
    for (const name of named) source.addEventListener(name, onEvent);

    return () => {
      closed = true;
      source.close();
    };
  }, [reloadConversation, runId, t]);

  useEffect(() => {
    if (!runId) return;
    let cancelled = false;
    async function tick() {
      if (!runId) return;
      const result = await loadAssistantRunClient(runId);
      if (cancelled || !result.ok) return;
      const waiting = result.data.tools.filter(
        (tool) => tool.status === "pending_approval" || tool.requiresApproval,
      );
      if (waiting.length > 0) {
        setPendingTools((current) => mergeTools(current, waiting));
        setStatus(t("waitingApproval"));
      }
      if (TERMINAL_RUN.has(result.data.status)) {
        setRunId(null);
        setStreamText("");
        setPendingTools([]);
        if (result.data.status === "completed") {
          setStatus(t("completed"));
        } else {
          const message = result.data.error || t("runFailed");
          setStatus(message);
          setError(message);
        }
        void reloadConversation();
      }
    }
    const timer = window.setInterval(() => {
      void tick();
    }, 2000);
    void tick();
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [reloadConversation, runId, t]);

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
    setSelectedServerId(result.data.serverId);
    setError(null);
    setStatus(null);
    setStreamText("");
    setPendingTools([]);
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
    setStatus(t("sending"));
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
      setStreamText("");
      setPendingTools([]);
      setRunId(result.data.id);
      setStatus(t("running"));
      const next = await loadAssistantConversationClient(conversationId);
      if (next.ok) setDetail(next.data);
    } finally {
      setPending(false);
    }
  }

  async function decide(tool: AssistantTool, decision: "approve" | "reject") {
    if (!runId) return;
    setPending(true);
    const result = await decideAssistantToolClient(
      runId,
      tool.id,
      decision,
      tool.argumentsHash,
    );
    setPending(false);
    if (!result.ok) {
      setError(result.error || t("failed"));
      return;
    }
    setPendingTools((current) => current.filter((item) => item.id !== tool.id));
    setStatus(decision === "approve" ? t("approved") : t("rejected"));
  }

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
                <Link href={`/servers/${selectedServerId}/discord` as Route}>
                  {t("openAgentPolicy")}
                </Link>
              </Button>
            ) : null}
          </CardContent>
        </Card>

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
              ) : detail.messages.length === 0 && !streamText ? (
                <p className="text-sm text-fg-muted">{t("noMessages")}</p>
              ) : (
                (detail?.messages ?? []).map((message) => (
                  <div key={message.id} className="space-y-1">
                    <p className="text-xs font-medium text-fg-subtle">
                      {message.role === "user"
                        ? t("roleUser")
                        : message.role === "assistant"
                          ? t("roleAssistant")
                          : message.role}
                    </p>
                    <p className="whitespace-pre-wrap text-sm text-fg">
                      {message.content || message.toolName || "—"}
                    </p>
                  </div>
                ))
              )}
              {streamText ? (
                <div className="space-y-1">
                  <p className="text-xs font-medium text-fg-subtle">{t("roleAssistant")}</p>
                  <p className="whitespace-pre-wrap text-sm text-fg">{streamText}</p>
                </div>
              ) : null}
            </div>

            {pendingTools.length > 0 ? (
              <div className="space-y-3">
                {pendingTools.map((tool) => (
                  <div
                    key={tool.id}
                    className="space-y-2 rounded-md border border-warn/40 bg-warn-muted/20 p-3"
                  >
                    <p className="text-sm font-medium text-fg">
                      {t("approvalRequired")}: {tool.toolName}
                    </p>
                    <pre className="max-h-40 overflow-auto rounded-md bg-canvas p-2 text-xs text-fg-muted">
                      {JSON.stringify(
                        { summary: tool.summary, arguments: tool.arguments },
                        null,
                        2,
                      )}
                    </pre>
                    <div className="flex flex-wrap gap-2">
                      <Button
                        type="button"
                        size="sm"
                        disabled={pending}
                        onClick={() => void decide(tool, "approve")}
                      >
                        {t("approve")}
                      </Button>
                      <Button
                        type="button"
                        size="sm"
                        variant="outline"
                        disabled={pending}
                        onClick={() => void decide(tool, "reject")}
                      >
                        {t("reject")}
                      </Button>
                    </div>
                  </div>
                ))}
              </div>
            ) : null}

            {status ? <p className="text-xs text-fg-subtle">{status}</p> : null}

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
                  setStreamText("");
                  setPendingTools([]);
                  setStatus(t("interrupt"));
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

function mergeTools(
  current: readonly AssistantTool[],
  incoming: readonly AssistantTool[],
): AssistantTool[] {
  const next = [...current];
  for (const tool of incoming) {
    const existing = next.find((item) => item.id === tool.id);
    if (!existing) {
      next.push(tool);
    } else if (
      Object.keys(existing.arguments).length === 0 &&
      Object.keys(tool.arguments).length > 0
    ) {
      next[next.indexOf(existing)] = tool;
    }
  }
  return next;
}
