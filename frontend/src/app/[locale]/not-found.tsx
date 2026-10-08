import { getLocale } from "next-intl/server";
import { resolveLocale } from "@/i18n/config";
import { recoveryMessages } from "@/i18n/recovery-messages";
import { RecoveryView } from "@/shared/ui/recovery-view";

export default async function NotFound() {
  const messages = recoveryMessages[resolveLocale(await getLocale(), null)];
  return (
    <RecoveryView
      code="404"
      title={messages.notFoundTitle}
      description={messages.notFoundDescription}
      overviewLabel={messages.overview}
    />
  );
}
