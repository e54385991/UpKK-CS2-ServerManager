"use client";

import { AlertHost } from "@/shared/feedback/alert-host";
import { ConfirmHost } from "@/shared/feedback/confirm-host";
import { lazy, Suspense, useSyncExternalStore } from "react";
import { getNotificationsServerSnapshot, getNotificationsSnapshot, subscribeNotifications } from "./notification-store";

const AppToaster = lazy(() => import("./toaster").then((module) => ({ default: module.AppToaster })));

export function FeedbackHost() {
  const showToaster = useSyncExternalStore(subscribeNotifications, getNotificationsSnapshot, getNotificationsServerSnapshot);
  return (
    <>
      {showToaster ? <Suspense fallback={null}><AppToaster /></Suspense> : null}
      <ConfirmHost />
      <AlertHost />
    </>
  );
}
