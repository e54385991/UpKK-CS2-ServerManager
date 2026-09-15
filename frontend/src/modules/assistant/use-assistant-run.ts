"use client";

import { useEffect, useState } from "react";
import { useTranslations } from "next-intl";
import { loadAssistantRunClient } from "@/modules/assistant/assistant-client";
import {
  parseAssistantSseData,
  toolFromApprovalPayload,
} from "@/modules/assistant/assistant-wire";
import { mergeTools } from "@/modules/assistant/assistant-tools";
import {
  EMPTY_TOKEN_USAGE,
  TERMINAL_RUN,
  tokenCount,
  type TokenUsage,
} from "@/modules/assistant/assistant-tokens";
import type { AssistantTool } from "@/modules/assistant/types";
import { createTextDisplayBuffer } from "@/shared/lib/render-coalesce";

export function useAssistantRun(
  runId: string | null,
  onConversationReload: () => void,
  onRunFinished: () => void,
) {
  const t = useTranslations("assistant");
  const [status, setStatus] = useState<string | null>(null);
  const [streamText, setStreamText] = useState("");
  const [pendingTools, setPendingTools] = useState<AssistantTool[]>([]);
  const [tokenUsage, setTokenUsage] = useState<TokenUsage>(EMPTY_TOKEN_USAGE);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!runId) return;
    let closed = false;
    const source = new EventSource(`/ai-stream/runs/${runId}`);
    const display = createTextDisplayBuffer((chunk) => {
      setStreamText((current) => current + chunk);
    });

    function onEvent(raw: MessageEvent<string>) {
      const event = parseAssistantSseData(raw.data);
      if (!event) return;
      if (event.type === "token_usage") {
        const input = tokenCount(event.payload.input_tokens);
        const output = tokenCount(event.payload.output_tokens);
        const total = tokenCount(event.payload.total_tokens) || input + output;
        const cached = tokenCount(event.payload.cached_input_tokens);
        const streaming = event.payload.streaming === true;
        setTokenUsage((current) => ({
          input: streaming ? Math.max(current.input, input) : input,
          output: streaming ? Math.max(current.output, output) : output,
          total: streaming ? Math.max(current.total, total) : total,
          cached: streaming ? current.cached : Math.max(current.cached, cached),
          estimated: event.payload.estimated !== false,
        }));
        return;
      }
      if (event.type === "run_started") {
        setStatus(t("thinking"));
        return;
      }
      if (event.type === "assistant_delta") {
        const delta = typeof event.payload.delta === "string" ? event.payload.delta : "";
        if (delta) display.append(delta);
        return;
      }
      display.flush();
      if (event.type === "assistant_message") {
        setStreamText("");
        onConversationReload();
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
      if (event.type === "run_retrying") {
        const attempt = tokenCount(event.payload.attempt);
        const maxAttempts = tokenCount(event.payload.max_attempts);
        setStatus(
          attempt && maxAttempts ? t("retrying", { attempt, maxAttempts }) : t("thinking"),
        );
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
      onRunFinished();
      setStreamText("");
      setPendingTools([]);
      setStatus(message);
      if (failed) setError(message);
      onConversationReload();
      source.close();
    }

    const named = [
      "run_started",
      "assistant_delta",
      "token_usage",
      "assistant_message",
      "tool_approval_required",
      "run_waiting_approval",
      "tool_started",
      "tool_queued",
      "run_retrying",
      "tool_progress",
      "diagnostic_progress",
      "run_completed",
      "run_failed",
      "run_interrupted",
    ];
    for (const name of named) source.addEventListener(name, onEvent);

    return () => {
      closed = true;
      display.dispose();
      source.close();
    };
  }, [onConversationReload, onRunFinished, runId, t]);

  useEffect(() => {
    if (!runId) return;
    let cancelled = false;
    let generation = 0;
    let inflight = false;

    async function tick() {
      if (inflight || cancelled || !runId) return;
      const requestId = ++generation;
      inflight = true;
      try {
        const result = await loadAssistantRunClient(runId);
        if (cancelled || requestId !== generation) return;
        if (!result.ok) return;
        const waiting = result.data.tools.filter(
          (tool) => tool.status === "pending_approval" || tool.requiresApproval,
        );
        if (waiting.length > 0) {
          setPendingTools((current) => mergeTools(current, waiting));
          setStatus(t("waitingApproval"));
        }
        if (TERMINAL_RUN.has(result.data.status)) {
          onRunFinished();
          setStreamText("");
          setPendingTools([]);
          if (result.data.status === "completed") {
            setStatus(t("completed"));
          } else {
            const message = result.data.error || t("runFailed");
            setStatus(message);
            setError(message);
          }
          onConversationReload();
        }
      } finally {
        if (requestId === generation) inflight = false;
      }
    }

    const timer = window.setInterval(() => {
      void tick();
    }, 2000);
    void tick();
    return () => {
      cancelled = true;
      generation += 1;
      inflight = false;
      window.clearInterval(timer);
    };
  }, [onConversationReload, onRunFinished, runId, t]);

  function resetLive() {
    setStreamText("");
    setPendingTools([]);
    setTokenUsage(EMPTY_TOKEN_USAGE);
    setStatus(null);
    setError(null);
  }

  function dropTool(toolId: string) {
    setPendingTools((current) => current.filter((item) => item.id !== toolId));
  }

  return {
    status,
    setStatus,
    streamText,
    pendingTools,
    tokenUsage,
    error,
    setError,
    resetLive,
    dropTool,
  };
}
