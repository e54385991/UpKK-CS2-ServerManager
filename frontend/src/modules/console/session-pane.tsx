"use client";

import { useEffect, useRef } from "react";
import type { Terminal } from "@xterm/xterm";
import type { FitAddon } from "@xterm/addon-fit";
import "@xterm/xterm/css/xterm.css";
import { paneDisplayText } from "@/modules/console/pane-client";
import type { ConsolePane } from "@/modules/console/types";
import { cn } from "@/shared/lib/cn";

export function SessionPane({
  pane,
  emptyText,
  className,
}: {
  pane: ConsolePane | null;
  emptyText: string;
  className?: string;
}) {
  const hostRef = useRef<HTMLDivElement>(null);
  const termRef = useRef<Terminal | null>(null);
  const fitRef = useRef<FitAddon | null>(null);
  const lastRef = useRef("");
  const paneRef = useRef(pane);
  const emptyRef = useRef(emptyText);

  useEffect(() => {
    paneRef.current = pane;
    emptyRef.current = emptyText;
  }, [emptyText, pane]);

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
        disableStdin: true,
        cursorBlink: false,
        fontSize: 13,
        theme: { background: "#0b0f14", foreground: "#e8edf5" },
      });
      const fit = new FitAddon();
      term.loadAddon(fit);
      term.open(hostRef.current);
      fit.fit();
      termRef.current = term;
      fitRef.current = fit;
      const display = paneDisplayText(paneRef.current);
      const next = display || emptyRef.current;
      lastRef.current = next;
      term.write(next.replace(/\n/g, "\r\n"));
    })();
    return () => {
      cancelled = true;
      termRef.current?.dispose();
      termRef.current = null;
      fitRef.current = null;
    };
  }, []);

  useEffect(() => {
    function onResize() {
      fitRef.current?.fit();
    }
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  useEffect(() => {
    const term = termRef.current;
    if (!term) return;
    const display = paneDisplayText(pane);
    const next = display || emptyText;
    if (next === lastRef.current) return;
    lastRef.current = next;
    term.reset();
    term.write(next.replace(/\n/g, "\r\n"));
  }, [emptyText, pane]);

  return (
    <div
      ref={hostRef}
      data-testid="session-pane"
      className={cn(
        "min-h-[24rem] overflow-hidden rounded-md border border-line bg-canvas p-2",
        className,
      )}
    />
  );
}
