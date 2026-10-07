"use client";

import { recoveryMessages } from "@/i18n/recovery-messages";
import { useRecoveryLocale } from "@/i18n/use-recovery-locale";
import { RecoveryView } from "@/shared/ui/recovery-view";
import "./globals.css";

export default function GlobalError({ retry }: { retry: () => void }) {
  const locale = useRecoveryLocale();
  const messages = recoveryMessages[locale];
  return (
    <html lang={locale}>
      <head><title>{messages.errorTitle}</title></head>
      <body style={{ fontFamily: "system-ui, sans-serif" }}>
        <RecoveryView
          title={messages.errorTitle}
          description={messages.errorDescription}
          overviewLabel={messages.overview}
          retryLabel={messages.retry}
          onRetry={retry}
        />
      </body>
    </html>
  );
}
