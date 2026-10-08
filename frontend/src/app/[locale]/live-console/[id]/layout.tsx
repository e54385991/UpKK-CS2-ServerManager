import { ClientMessages } from "@/i18n/client-messages";
import { LIVE_CONSOLE_NAMESPACES } from "@/i18n/namespaces";
import { Suspense } from "react";
import { SessionGate } from "@/modules/shell/session-slots";

export default function LiveConsoleLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <ClientMessages namespaces={LIVE_CONSOLE_NAMESPACES}>
      <Suspense fallback={null}><SessionGate /></Suspense>
      <div className="min-h-0 flex-1 overflow-hidden bg-canvas p-3">{children}</div>
    </ClientMessages>
  );
}
