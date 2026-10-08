import { redirect } from "@/i18n/redirect";
import { getSession, loginReturnPath } from "@/modules/auth/render-session";
import { getPublishedAnnouncements } from "@/modules/announcements/api";
import { AnnouncementCenter } from "@/modules/announcements/announcement-center";
import { CS2UpdateNoticePopover } from "@/modules/announcements/cs2-update-notice-popover";
import { AdminNavigation } from "./admin-navigation";
import { UserMenu } from "./user-menu";
import { ActivityTray } from "./activity-tray";
import { SshPoolBadge } from "./ssh-pool-badge";

/** Redirects only; each backend read and mutation still authorizes its request. */
export async function SessionGate() {
  if (!await getSession()) redirect(await loginReturnPath());
  return null;
}
export async function AdminNavigationSlot() {
  const user = await getSession();
  return user?.isAdmin ? <AdminNavigation /> : null;
}
export async function UserMenuSlot() {
  const user = await getSession();
  return user ? <UserMenu user={user} /> : null;
}
export async function ActivityTraySlot() {
  const user = await getSession();
  return user ? <ActivityTray isAdmin={user.isAdmin} /> : null;
}
export async function SshPoolSlot() {
  return await getSession() ? <SshPoolBadge /> : null;
}
export async function AnnouncementSlot() {
  const feed = await getPublishedAnnouncements();
  return <>
    <AnnouncementCenter announcements={feed.ok ? feed.data.items : []} />
    {feed.ok && feed.data.cs2UpdateNotice ? <CS2UpdateNoticePopover notice={feed.data.cs2UpdateNotice} /> : null}
  </>;
}
