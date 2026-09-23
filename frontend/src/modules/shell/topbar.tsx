import { getTranslations } from "next-intl/server";
import { MobileNav } from "@/modules/shell/mobile-nav";
import { UserMenu } from "@/modules/shell/user-menu";
import { LanguageSwitcher } from "@/modules/shell/language-switcher";
import { StatusDot } from "@/shared/ui/badge";
import { ActivityTray } from "@/modules/shell/activity-tray";
import { SshPoolBadge } from "@/modules/shell/ssh-pool-badge";
import type { SessionUser } from "@/modules/auth/session";
import type {
  Announcement,
  CS2UpdateNotice,
} from "@/modules/announcements/types";
import { AnnouncementCenter } from "@/modules/announcements/announcement-center";
import { CS2UpdateNoticePopover } from "@/modules/announcements/cs2-update-notice-popover";

/**
 * Persistent top bar. Holds the mobile navigation trigger, a live gateway
 * status indicator, the language switcher, and the user menu. Rendered once by
 * the console layout so it never unmounts during navigation.
 */
export async function Topbar({
  user,
  announcements,
  cs2UpdateNotice,
}: {
  user: SessionUser;
  announcements: readonly Announcement[];
  cs2UpdateNotice: CS2UpdateNotice | null;
}) {
  const t = await getTranslations("shell");
  return (
    <header className="sticky top-0 z-30 flex h-14 items-center gap-3 border-b border-line bg-canvas/80 px-4 backdrop-blur-md">
      <MobileNav isAdmin={user.isAdmin} />

      <div className="flex items-center gap-3 text-xs text-fg-subtle">
        <span className="inline-flex items-center gap-2">
          <StatusDot tone="ok" pulse />
          <span className="hidden sm:inline">{t("gatewayOnline")}</span>
        </span>
        <SshPoolBadge />
      </div>

      <div className="ml-auto flex items-center gap-2">
        <AnnouncementCenter
          announcements={announcements}
        />
        {cs2UpdateNotice ? (
          <CS2UpdateNoticePopover notice={cs2UpdateNotice} />
        ) : null}
        <ActivityTray isAdmin={user.isAdmin} />
        <LanguageSwitcher />
        <UserMenu user={user} />
      </div>
    </header>
  );
}
