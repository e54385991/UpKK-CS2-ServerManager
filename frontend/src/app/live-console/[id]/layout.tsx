import { ClientMessages } from "@/i18n/client-messages";
import { LIVE_CONSOLE_NAMESPACES } from "@/i18n/namespaces";
import { requireSession } from "@/modules/auth/render-session";

export default async function LiveConsoleLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  await requireSession();
  return (
    <ClientMessages namespaces={LIVE_CONSOLE_NAMESPACES}>
      <div className="min-h-0 flex-1 overflow-hidden bg-canvas p-3">{children}</div>
    </ClientMessages>
  );
}
