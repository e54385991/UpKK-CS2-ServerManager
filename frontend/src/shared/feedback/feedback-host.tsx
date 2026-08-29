"use client";

import { ConfirmHost } from "@/shared/feedback/confirm-host";
import { AppToaster } from "@/shared/feedback/toaster";

export function FeedbackHost() {
  return (
    <>
      <AppToaster />
      <ConfirmHost />
    </>
  );
}
