import { ClientMessages } from "@/i18n/client-messages";
import { CONSOLE_NAMESPACES } from "@/i18n/namespaces";
import { requireSession } from "@/modules/auth/render-session";
import { ConsoleShell } from "@/modules/shell/console-shell";

/**
 * Console App Shell. The sidebar and top bar are rendered here and stay mounted
 * across every child route, so navigating between console pages swaps only the
 * page region — the chrome never blanks out. Per-route `loading.tsx` files fill
 * that region with an instant skeleton while server data streams in.
 */
export default async function ConsoleLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const user = await requireSession();
  return (
    <ClientMessages namespaces={CONSOLE_NAMESPACES}>
      <ConsoleShell user={user}>{children}</ConsoleShell>
    </ClientMessages>
  );
}
