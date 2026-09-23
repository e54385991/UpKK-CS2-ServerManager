"use client";

import { useFormatter, useTranslations } from "next-intl";
import type { Announcement } from "@/modules/announcements/types";
import { Dialog } from "@/shared/ui/dialog";
import { Markdown } from "@/shared/ui/markdown";

export function AnnouncementDialog({
  announcements,
  onClose,
}: {
  announcements: readonly Announcement[];
  onClose: () => void;
}) {
  const format = useFormatter();
  const t = useTranslations("shell");

  return (
    <Dialog
      open
      title={t("announcementsTitle")}
      closeLabel={t("announcementsClose")}
      onClose={onClose}
      className="max-w-4xl"
    >
      {announcements.map((announcement) => (
        <article
          key={announcement.id}
          className="space-y-3 border-b border-line pb-5 last:border-b-0 last:pb-0 [&+article]:pt-5"
        >
          <header className="space-y-1">
            <h3 className="text-base font-semibold text-fg">{announcement.title}</h3>
            {announcement.publishedAt ? (
              <time
                dateTime={announcement.publishedAt}
                className="text-xs text-fg-subtle"
              >
                {format.dateTime(new Date(announcement.publishedAt), {
                  dateStyle: "medium",
                  timeStyle: "short",
                })}
              </time>
            ) : null}
          </header>
          <Markdown source={announcement.bodyMarkdown} />
        </article>
      ))}
    </Dialog>
  );
}
