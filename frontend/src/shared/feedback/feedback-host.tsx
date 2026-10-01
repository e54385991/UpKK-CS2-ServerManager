"use client";

import { lazy, Suspense, useSyncExternalStore } from "react";
import { getAlertServerSnapshot, getAlertSnapshot, subscribeAlert } from "./alert-store";
import { getConfirmServerSnapshot, getConfirmSnapshot, subscribeConfirm } from "./confirm-store";
import {
  getNotificationsServerSnapshot,
  getNotificationsSnapshot,
  subscribeNotifications,
} from "./notification-store";

const AppToaster = lazy(() => import("./toaster").then((module) => ({ default: module.AppToaster })));
const ConfirmHost = lazy(() => import("./confirm-host").then((module) => ({ default: module.ConfirmHost })));
const AlertHost = lazy(() => import("./alert-host").then((module) => ({ default: module.AlertHost })));

export function FeedbackHost() {
  const showToaster = useSyncExternalStore(
    subscribeNotifications,
    getNotificationsSnapshot,
    getNotificationsServerSnapshot,
  );
  const confirmation = useSyncExternalStore(subscribeConfirm, getConfirmSnapshot, getConfirmServerSnapshot);
  const alert = useSyncExternalStore(subscribeAlert, getAlertSnapshot, getAlertServerSnapshot);

  return (
    <>
      <Suspense fallback={null}>{showToaster ? <AppToaster /> : null}</Suspense>
      <Suspense fallback={null}>{confirmation ? <ConfirmHost /> : null}</Suspense>
      <Suspense fallback={null}>{alert ? <AlertHost /> : null}</Suspense>
    </>
  );
}
