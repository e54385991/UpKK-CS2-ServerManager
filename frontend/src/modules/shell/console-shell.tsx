import { Sidebar } from "@/modules/shell/sidebar";
import { Topbar } from "@/modules/shell/topbar";
import type { SessionUser } from "@/modules/auth/session";
import type { Announcement } from "@/modules/announcements/types";
import type { CS2UpdateNotice } from "@/modules/announcements/types";

/**
 * Authenticated chrome shared by the console group and public-but-signed-in
 * pages (the deployment tutorial). Sidebar + topbar stay on screen; only the
 * main region scrolls.
 */
export function ConsoleShell({
  user,
  announcements,
  cs2UpdateNotice,
  children,
}: {
  user: SessionUser;
  announcements: readonly Announcement[];
  cs2UpdateNotice: CS2UpdateNotice | null;
  children: React.ReactNode;
}) {
  return (
    <div className="flex min-h-0 flex-1 overflow-hidden">
      <Sidebar isAdmin={user.isAdmin} />
      <div className="flex min-h-0 min-w-0 flex-1 flex-col">
        <Topbar
          user={user}
          announcements={announcements}
          cs2UpdateNotice={cs2UpdateNotice}
        />
        <main className="min-h-0 flex-1 overflow-y-auto px-4 py-6 sm:px-6 lg:px-8">
          <div className="mx-auto w-full max-w-7xl">{children}</div>
        </main>
      </div>
    </div>
  );
}
