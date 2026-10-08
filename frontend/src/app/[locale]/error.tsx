"use client";

import { recoveryMessages } from "@/i18n/recovery-messages";
import { useLocale } from "next-intl";
import { RecoveryView } from "@/shared/ui/recovery-view";

export default function ErrorPage({ retry }: { retry: () => void }) {
  const messages = recoveryMessages[useLocale()];
  return (
    <RecoveryView
      title={messages.errorTitle}
      description={messages.errorDescription}
      overviewLabel={messages.overview}
      retryLabel={messages.retry}
      onRetry={retry}
    />
  );
}
