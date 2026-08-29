"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import type { Terminal } from "@xterm/xterm";
import type { FitAddon } from "@xterm/addon-fit";
import "@xterm/xterm/css/xterm.css";
import {
  RefreshCw,
  SquareTerminal,
  TriangleAlert,
  Unplug,
} from "lucide-react";
import { refreshConsoleAction } from "@/modules/console/actions";
import type { ConsoleKind, ConsoleWorkspace } from "@/modules/console/types";
import { Badge } from "@/shared/ui/badge";
import { Button } from "@/shared/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/shared/ui/card";
import { cn } from "@/shared/lib/cn";

type Banner = { readonly tone: "ok" | "warn" | "danger"; readonly text: string };

function ConsoleTerminal({
  serverId,
  kind,
  disabled,
  hint,
}: {
  serverId: number;
  kind: ConsoleKind;
  disabled: boolean;
  hint: string;
}) {
  const t = useTranslations("console");
  const [status, setStatus] = useState<"idle" | "connecting" | "open" | "closed">("idle");
  const socketRef = useRef<WebSocket | null>(null);
  const hostRef = useRef<HTMLDivElement>(null);
  const termRef = useRef<Terminal | null>(null);
  const fitRef = useRef<FitAddon | null>(null);

  const write = useCallback((chunk: string) => {
    termRef.current?.write(chunk.replace(/\n/g, "\r\n"));
  }, []);

  const sendResize = useCallback(() => {
    const term = termRef.current;
    const socket = socketRef.current;
    if (!term || socket?.readyState !== WebSocket.OPEN) return;
    socket.send(JSON.stringify({ type: "resize", cols: term.cols, rows: term.rows }));
  }, []);

  const disconnect = useCallback(() => {
    const socket = socketRef.current;
    socketRef.current = null;
    if (socket && socket.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify({ type: "disconnect" }));
      socket.close();
    }
    setStatus((current) => (current === "idle" ? current : "closed"));
  }, []);

  const connect = useCallback(() => {
    if (disabled) return;
    disconnect();
    termRef.current?.reset();
    setStatus("connecting");
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const socket = new WebSocket(
      `${protocol}//${window.location.host}/api/v1/servers/${serverId}/console/${kind}`,
    );
    socketRef.current = socket;
    socket.addEventListener("message", (event) => {
      try {
        const payload = JSON.parse(String(event.data)) as {
          type?: string;
          data?: string;
          message?: string;
        };
        if (payload.type === "output" && payload.data) write(payload.data);
        if (payload.type === "connected") {
          setStatus("open");
          if (payload.message) write(`${payload.message}\r\n`);
          sendResize();
        }
        if (payload.type === "error") {
          setStatus("closed");
          write(`${payload.message || t("failed")}\r\n`);
        }
      } catch {
        write(String(event.data));
      }
    });
    socket.addEventListener("open", () => setStatus("open"));
    socket.addEventListener("close", () => {
      socketRef.current = null;
      setStatus("closed");
    });
    socket.addEventListener("error", () => {
      write(`${t("socketFailed")}\r\n`);
      setStatus("closed");
    });
  }, [disabled, disconnect, kind, sendResize, serverId, t, write]);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const [{ Terminal }, { FitAddon }] = await Promise.all([
        import("@xterm/xterm"),
        import("@xterm/addon-fit"),
      ]);
      if (cancelled || !hostRef.current) return;
      const term = new Terminal({
        convertEol: true,
        cursorBlink: true,
        fontSize: 13,
        theme: { background: "#0b0f14", foreground: "#e8edf5" },
      });
      const fit = new FitAddon();
      term.loadAddon(fit);
      term.open(hostRef.current);
      fit.fit();
      term.writeln(hint);
      term.onData((data) => {
        if (socketRef.current?.readyState === WebSocket.OPEN) {
          socketRef.current.send(JSON.stringify({ type: "input", data }));
        }
      });
      termRef.current = term;
      fitRef.current = fit;
    })();
    return () => {
      cancelled = true;
      disconnect();
      termRef.current?.dispose();
      termRef.current = null;
      fitRef.current = null;
    };
  }, [disconnect, hint]);

  useEffect(() => {
    if (status !== "open") return;
    const id = window.setInterval(() => {
      if (socketRef.current?.readyState === WebSocket.OPEN) {
        socketRef.current.send(JSON.stringify({ type: "ping" }));
      }
    }, 20000);
    return () => window.clearInterval(id);
  }, [status]);

  useEffect(() => {
    function onResize() {
      fitRef.current?.fit();
      sendResize();
    }
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, [sendResize]);

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-2">
        <Button
          type="button"
          size="sm"
          disabled={disabled || status === "connecting" || status === "open"}
          onClick={connect}
        >
          <SquareTerminal />
          {status === "connecting" ? t("connecting") : t("connect")}
        </Button>
        <Button
          type="button"
          size="sm"
          variant="outline"
          disabled={status !== "open"}
          onClick={disconnect}
        >
          <Unplug />
          {t("disconnect")}
        </Button>
      </div>
      <div
        ref={hostRef}
        className={cn(
          "h-80 overflow-hidden rounded-md border border-line bg-canvas p-2",
          disabled && "opacity-60",
        )}
      />
    </div>
  );
}

export function ConsoleWorkspaceView({ initial }: { initial: ConsoleWorkspace }) {
  const t = useTranslations("console");
  const router = useRouter();
  const [workspace, setWorkspace] = useState(initial);
  const [pending, setPending] = useState(false);
  const [banner, setBanner] = useState<Banner | null>(
    initial.message ? { tone: "ok", text: initial.message } : null,
  );

  async function refresh() {
    setPending(true);
    const result = await refreshConsoleAction(workspace.serverId);
    setPending(false);
    if (!result.ok) {
      setBanner({ tone: "danger", text: result.error || t("failed") });
      return;
    }
    setWorkspace(result.data);
    setBanner(result.data.message ? { tone: "ok", text: result.data.message } : null);
    router.refresh();
  }

  return (
    <div className="space-y-6">
      {banner ? (
        <p
          className={cn(
            "rounded-lg border px-4 py-3 text-sm",
            banner.tone === "ok" && "border-ok/30 bg-ok-muted/40 text-ok",
            banner.tone === "warn" && "border-warn/30 bg-warn-muted/40 text-warn",
            banner.tone === "danger" && "border-danger/30 bg-danger-muted/40 text-danger",
          )}
        >
          {banner.text}
        </p>
      ) : null}

      {!workspace.sshOk ? (
        <Card className="border-danger/30 bg-danger-muted/20">
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-danger">
              <TriangleAlert className="size-4" />
              {t("sshDown")}
            </CardTitle>
            <CardDescription>{workspace.sshError || t("sshDownHelp")}</CardDescription>
          </CardHeader>
        </Card>
      ) : null}

      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap gap-2">
          <Badge tone={workspace.sshOk ? "ok" : "danger"}>
            {workspace.sshOk ? t("sshUp") : t("sshDown")}
          </Badge>
          <Badge tone={workspace.gameRunning ? "ok" : "neutral"}>
            {workspace.gameRunning ? t("gameUp") : t("gameDown")}
          </Badge>
          <Badge>{workspace.sessionManager}</Badge>
          <Badge tone="neutral">{workspace.host}</Badge>
        </div>
        <Button type="button" variant="outline" size="sm" disabled={pending} onClick={() => void refresh()}>
          <RefreshCw />
          {t("refresh")}
        </Button>
      </div>

      <div className="grid gap-6 xl:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>{t("gameTitle")}</CardTitle>
            <CardDescription>{t("gameHelp")}</CardDescription>
          </CardHeader>
          <CardContent>
            <ConsoleTerminal
              serverId={workspace.serverId}
              kind="game"
              disabled={!workspace.sshOk || !workspace.gameRunning}
              hint={
                !workspace.sshOk
                  ? t("listLocked")
                  : workspace.gameRunning
                    ? t("gameHint")
                    : t("gameNotRunning")
              }
            />
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>{t("sshTitle")}</CardTitle>
            <CardDescription>{t("sshHelp")}</CardDescription>
          </CardHeader>
          <CardContent>
            <ConsoleTerminal
              serverId={workspace.serverId}
              kind="ssh"
              disabled={!workspace.sshOk}
              hint={workspace.sshOk ? t("sshHint") : t("listLocked")}
            />
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
