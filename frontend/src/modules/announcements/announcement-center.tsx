"use client";

import { useState } from "react";
import dynamic from "next/dynamic";
import { useTranslations } from "next-intl";
import type { Announcement } from "@/modules/announcements/types";
import { Button } from "@/shared/ui/button";

const AnnouncementDialog = dynamic(
  () =>
    import("@/modules/announcements/announcement-content").then(
      (module) => module.AnnouncementDialog,
    ),
  { loading: () => null },
);

export function AnnouncementCenter({
  announcements,
}: {
  announcements: readonly Announcement[];
}) {
  const t = useTranslations("shell");
  const [open, setOpen] = useState(false);

  if (announcements.length === 0) return null;

  return (
    <>
      <Button
        type="button"
        variant="outline"
        size="sm"
        onClick={() => setOpen(true)}
        className="gap-1.5"
      >
        <span>{t("announcementsTitle")}</span>
      </Button>
      {open ? (
        <AnnouncementDialog
          announcements={announcements}
          onClose={() => setOpen(false)}
        />
      ) : null}
    </>
  );
}
