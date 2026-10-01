import assert from "node:assert/strict";
import test from "node:test";
import {
  getNotificationsSnapshot,
  getNotificationsServerSnapshot,
  publishNotification,
  registerNotificationRenderer,
  subscribeNotifications,
  type Notification,
} from "./notification-store.ts";

test("feedback before renderer loading is retained, delivered once in order, and survives remount", () => {
  assert.equal(getNotificationsSnapshot(), false);
  assert.equal(getNotificationsServerSnapshot(), false);
  let wakeups = 0;
  const unsubscribe = subscribeNotifications(() => { wakeups += 1; });
  const first: Notification = { kind: "success", message: "first" };
  const second: Notification = { kind: "dismiss", id: 5 };
  publishNotification(first);
  publishNotification(second);
  assert.equal(wakeups, 1);
  assert.equal(getNotificationsSnapshot(), true);
  const received: Notification[] = [];
  const unregister = registerNotificationRenderer((item) => received.push(item));
  assert.deepEqual(received, [first, second]);
  publishNotification({ kind: "error", message: "after mount" });
  assert.equal(received.length, 3);
  unregister();
  publishNotification({ kind: "message", message: "during remount" });
  assert.equal(received.length, 3);
  const detach = registerNotificationRenderer((item) => received.push(item));
  assert.equal(received.length, 4);
  assert.deepEqual(received[3], { kind: "message", message: "during remount" });
  detach();
  unsubscribe();
});
