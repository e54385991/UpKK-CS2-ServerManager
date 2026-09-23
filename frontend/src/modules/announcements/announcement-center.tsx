"use client";

import { useState } from "react";
import dynamic from "next/dynamic";
import { useTranslations } from "next-intl";
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

const CS2UpdateNoticeCenter = dynamic(
  () =>
    import("@/modules/announcements/cs2-update-notice-center").then(
      (module) => module.CS2UpdateNoticeCenter,
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
      {announcementsOpen ? (
        <AnnouncementDialog
          announcements={announcements}
          onClose={() => setAnnouncementsOpen(false)}
        />
      ) : null}
      {cs2UpdateNotice ? (
        <CS2UpdateNoticeCenter notice={cs2UpdateNotice} />
      ) : null}
    </>
  );
}
