"use client";

import { useEffect, useState } from "react";
import dynamic from "next/dynamic";
import { useTranslations } from "next-intl";
import { TriangleAlert } from "lucide-react";
import type {
  Announcement,
  CS2UpdateNotice,
} from "@/modules/announcements/types";
import { Button } from "@/shared/ui/button";

const AnnouncementDialog = dynamic(
  () =>
    import("@/modules/announcements/announcement-content").then(
      (module) => module.AnnouncementDialog,
    ),
  { loading: () => null },
);

const CS2UpdateNoticeDialog = dynamic(
  () =>
    import("@/modules/announcements/announcement-content").then(
      (module) => module.CS2UpdateNoticeDialog,
    ),
  { loading: () => null },
);

export function AnnouncementCenter({
  announcements,
  cs2UpdateNotice,
}: {
  announcements: readonly Announcement[];
  cs2UpdateNotice: CS2UpdateNotice | null;
}) {
  const t = useTranslations("shell");
  const [announcementsOpen, setAnnouncementsOpen] = useState(false);
  const [noticeOpen, setNoticeOpen] = useState(false);
  const [expiredNoticeAt, setExpiredNoticeAt] = useState<string | null>(null);
  const noticeExpiresAt = cs2UpdateNotice?.expiresAt;
  const noticeExpired = noticeExpiresAt === expiredNoticeAt;

  useEffect(() => {
    if (!noticeExpiresAt) return;
    const remaining = Date.parse(noticeExpiresAt) - Date.now();
    const timer = window.setTimeout(
      () => setExpiredNoticeAt(noticeExpiresAt),
      Math.max(0, remaining),
    );
    return () => window.clearTimeout(timer);
  }, [noticeExpiresAt]);

  if (announcements.length === 0 && (!cs2UpdateNotice || noticeExpired)) {
    return null;
  }

  return (
    <>
      {announcements.length > 0 ? (
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() => setAnnouncementsOpen(true)}
          className="gap-1.5"
        >
          <span>{t("announcementsTitle")}</span>
        </Button>
      ) : null}
      {cs2UpdateNotice && !noticeExpired ? (
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() => setNoticeOpen(true)}
          aria-label={t("cs2UpdateNoticeButton")}
          title={t("cs2UpdateNoticeButton")}
          className="gap-1.5 border-warn/40 bg-warn-muted/50 px-2 text-warn hover:bg-warn-muted sm:px-3"
        >
          <TriangleAlert aria-hidden="true" />
          <span className="hidden sm:inline">{t("cs2UpdateNoticeButton")}</span>
        </Button>
      ) : null}
      {announcementsOpen ? (
        <AnnouncementDialog
          announcements={announcements}
          onClose={() => setAnnouncementsOpen(false)}
        />
      ) : null}
      {noticeOpen && cs2UpdateNotice && !noticeExpired ? (
        <CS2UpdateNoticeDialog
          notice={cs2UpdateNotice}
          onClose={() => setNoticeOpen(false)}
        />
      ) : null}
    </>
  );
}
