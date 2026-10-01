"use client";

import { useEffect } from "react";
import { Toaster, toast } from "sonner";
import { registerNotificationRenderer } from "./notification-store";

export function AppToaster() {
  useEffect(() => registerNotificationRenderer((notification) => {
    if (notification.kind === "dismiss") toast.dismiss(notification.id);
    else if (notification.kind === "message") toast(notification.message);
    else if (notification.kind === "error") toast.error(notification.message, { duration: 6000 });
    else toast[notification.kind](notification.message);
  }), []);
  return (
    <Toaster
      theme="dark"
      position="top-right"
      closeButton
      offset={16}
      gap={8}
      duration={4000}
      toastOptions={{
        classNames: {
          toast:
            "!border !border-line !bg-surface-overlay !text-fg shadow-panel",
          title: "!text-fg !font-medium",
          description: "!text-fg-muted",
          actionButton: "!bg-primary !text-primary-foreground",
          cancelButton: "!bg-surface-raised !text-fg-muted",
          closeButton:
            "!border-line !bg-surface-raised !text-fg-muted hover:!text-fg",
          success: "!border-ok/35",
          error: "!border-danger/40",
          warning: "!border-warn/40",
          info: "!border-info/35",
        },
      }}
    />
  );
}
