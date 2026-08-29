import { MobileNav } from "@/modules/shell/mobile-nav";
import { UserMenu } from "@/modules/shell/user-menu";
import { StatusDot } from "@/shared/ui/badge";
import type { SessionUser } from "@/modules/auth/session";

/**
 * Persistent top bar. Holds the mobile navigation trigger, a live gateway
 * status indicator, and the user menu. Rendered once by the console layout so
 * it never unmounts during navigation.
 */
export function Topbar({ user }: { user: SessionUser }) {
  return (
    <header className="sticky top-0 z-30 flex h-14 items-center gap-3 border-b border-line bg-canvas/80 px-4 backdrop-blur-md">
      <MobileNav isAdmin={user.isAdmin} />

      <div className="flex items-center gap-2 text-xs text-fg-subtle">
        <StatusDot tone="ok" pulse />
        <span className="hidden sm:inline">网关在线</span>
      </div>

      <div className="ml-auto flex items-center gap-2">
        <UserMenu user={user} />
      </div>
    </header>
  );
}
