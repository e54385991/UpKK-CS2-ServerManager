/** Buffer action feedback until the optional toast renderer has mounted. */
export type Notification =
  | { kind: "success" | "error" | "warning" | "info" | "message"; message: string }
  | { kind: "dismiss"; id?: string | number };

type Renderer = (notification: Notification) => void;
let renderer: Renderer | null = null;
let requested = false;
const pending: Notification[] = [];
const listeners = new Set<() => void>();

export function subscribeNotifications(listener: () => void) {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}

export function getNotificationsSnapshot() { return requested; }
export function getNotificationsServerSnapshot() { return false; }

export function publishNotification(notification: Notification) {
  if (renderer) {
    renderer(notification);
    return;
  }
  pending.push(notification);
  if (!requested) {
    requested = true;
    for (const listener of listeners) listener();
  }
}

export function registerNotificationRenderer(next: Renderer) {
  renderer = next;
  while (pending.length > 0) {
    const notification = pending.shift();
    if (notification) next(notification);
  }
  return () => { if (renderer === next) renderer = null; };
}
