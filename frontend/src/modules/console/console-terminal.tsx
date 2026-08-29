"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslations } from "next-intl";
import type { Terminal } from "@xterm/xterm";
import type { FitAddon } from "@xterm/addon-fit";
import "@xterm/xterm/css/xterm.css";
import { SquareTerminal, Unplug } from "lucide-react";
import { paneDisplayText } from "@/modules/console/pane-client";
import type { ConsoleKind, ConsolePane } from "@/modules/console/types";
import { Button } from "@/shared/ui/button";
import { cn } from "@/shared/lib/cn";

export function ConsoleTerminal({
  serverId,
  kind,
  disabled,
  hint,
  autoConnect = false,
  seedPane = null,
  className,
}: {
  serverId: number;
  kind: ConsoleKind;
  disabled: boolean;
  hint: string;
  autoConnect?: boolean;
  seedPane?: ConsolePane | null;
  className?: string;
}) {
  const t = useTranslations("console");
  const [status, setStatus] = useState<"idle" | "connecting" | "open" | "closed">(
    "idle",
  );
  const [ready, setReady] = useState(false);
  const socketRef = useRef<WebSocket | null>(null);
  const hostRef = useRef<HTMLDivElement>(null);
  const termRef = useRef<Terminal | null>(null);
  const fitRef = useRef<FitAddon | null>(null);
  const seededRef = useRef(false);
  const seedPaneRef = useRef(seedPane);

  useEffect(() => {
    seedPaneRef.current = seedPane;
  }, [seedPane]);

  const write = useCallback((chunk: string) => {
    termRef.current?.write(chunk.replace(/\n/g, "\r\n"));
  }, []);

  const writeSeed = useCallback(
    (pane: ConsolePane | null) => {
      const seed = paneDisplayText(pane);
      if (!seed || !termRef.current) return;
      termRef.current.reset();
      write(seed.endsWith("\n") ? seed : `${seed}\n`);
      seededRef.current = true;
    },
    [write],
  );

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
    seededRef.current = false;
    writeSeed(seedPaneRef.current);
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
        if (payload.type === "output" && payload.data) {
          if (seededRef.current) {
            termRef.current?.reset();
            seededRef.current = false;
          }
          write(payload.data);
        }
        if (payload.type === "connected") {
          setStatus("open");
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
  }, [disabled, disconnect, kind, sendResize, serverId, t, write, writeSeed]);

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
      const seed = paneDisplayText(seedPaneRef.current);
      if (seed) {
        term.write(seed.replace(/\n/g, "\r\n"));
        if (!seed.endsWith("\n")) term.write("\r\n");
        seededRef.current = true;
      } else {
        term.writeln(hint);
      }
      term.onData((data) => {
        if (socketRef.current?.readyState === WebSocket.OPEN) {
          socketRef.current.send(JSON.stringify({ type: "input", data }));
        }
      });
      termRef.current = term;
      fitRef.current = fit;
      setReady(true);
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
    if (!autoConnect || disabled || !ready || status !== "idle") return;
    const id = window.setTimeout(() => {
      connect();
    }, 0);
    return () => window.clearTimeout(id);
  }, [autoConnect, connect, disabled, ready, status]);

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
          "overflow-hidden rounded-md border border-line bg-canvas p-2",
          autoConnect ? "min-h-[calc(100dvh-14rem)]" : "h-80",
          disabled && "opacity-60",
          className,
        )}
      />
    </div>
  );
}
