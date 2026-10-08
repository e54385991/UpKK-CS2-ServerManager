import { Suspense } from "react";
import { ClientMessages } from "@/i18n/client-messages";
import { CONSOLE_NAMESPACES } from "@/i18n/namespaces";
import { ConsoleShell } from "@/modules/shell/console-shell";
import { SessionGate } from "@/modules/shell/session-slots";

export default function ConsoleLayout({ children }: { children: React.ReactNode }) {
  return (
    <ClientMessages namespaces={CONSOLE_NAMESPACES}>
      <Suspense fallback={null}><SessionGate /></Suspense>
      <ConsoleShell>{children}</ConsoleShell>
    </ClientMessages>
  );
}
