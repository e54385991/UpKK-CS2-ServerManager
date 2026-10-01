"use client";

import { publishNotification } from "./notification-store";

/** App-wide toast API. Use for action results, not form-field validation. */
export const notify = {
  success(message: string) { publishNotification({ kind: "success", message }); },
  error(message: string) { publishNotification({ kind: "error", message }); },
  warning(message: string) { publishNotification({ kind: "warning", message }); },
  info(message: string) { publishNotification({ kind: "info", message }); },
  message(message: string) { publishNotification({ kind: "message", message }); },
  dismiss(id?: string | number) { publishNotification({ kind: "dismiss", id }); },
};
