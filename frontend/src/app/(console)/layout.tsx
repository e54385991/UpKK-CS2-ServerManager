import { requireSession } from "@/modules/auth/session";
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
  return <ConsoleShell user={user}>{children}</ConsoleShell>;
}
