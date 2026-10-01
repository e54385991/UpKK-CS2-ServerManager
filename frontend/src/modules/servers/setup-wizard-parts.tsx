"use client";

import { Label } from "@/shared/ui/input";
import {
  type ReactNode
} from "react";

export type Captcha = { token: string; imageUrl: string; enabled: boolean };

export type Mode = "auto" | "manual";

export function openSetupProgressSocket(
  sessionId: string,
  appendLog: (message: string) => void,
): WebSocket {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  const socket = new WebSocket(
    `${protocol}//${window.location.host}/api/setup/setup-progress/${sessionId}`,
  );
  socket.addEventListener("message", (event) => {
    try {
      const payload = JSON.parse(String(event.data)) as { message?: string };
      if (payload.message) appendLog(payload.message);
    } catch {
      appendLog(String(event.data));
    }
  });
  return socket;
}

export function waitForSocket(
  socket: WebSocket,
  onOpen: () => void,
  onError: () => void,
): Promise<void> {
  return new Promise((resolve) => {
    let settled = false;
    const finish = () => {
      if (settled) return;
      settled = true;
      resolve();
    };
    const timer = window.setTimeout(finish, 1500);
    socket.addEventListener("open", () => {
      window.clearTimeout(timer);
      onOpen();
      finish();
    });
    socket.addEventListener("error", () => {
      window.clearTimeout(timer);
      onError();
      finish();
    });
  });
}

export function Field({
  label,
  htmlFor,
  children,
}: {
  label: string;
  htmlFor: string;
  children: ReactNode;
}) {
  return (
    <div>
      <Label htmlFor={htmlFor}>{label}</Label>
      {children}
    </div>
  );
}

