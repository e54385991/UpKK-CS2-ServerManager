import { requireSession } from "@/modules/auth/session";
import { Sidebar } from "@/modules/shell/sidebar";
import { Topbar } from "@/modules/shell/topbar";

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
    <div className="flex min-h-0 flex-1 overflow-hidden">
      <Sidebar isAdmin={user.isAdmin} />
      <div className="flex min-h-0 min-w-0 flex-1 flex-col">
        <Topbar user={user} />
        <main className="min-h-0 flex-1 overflow-y-auto px-4 py-6 sm:px-6 lg:px-8">
          <div className="mx-auto w-full max-w-7xl">{children}</div>
        </main>
      </div>
    </div>
  );
}
