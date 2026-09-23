"use client";

import { useFormatter, useTranslations } from "next-intl";
import type {
  Announcement,
  CS2UpdateNotice,
} from "@/modules/announcements/types";
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

export function CS2UpdateNoticeDialog({
  notice,
  onClose,
}: {
  notice: CS2UpdateNotice;
  onClose: () => void;
}) {
  const format = useFormatter();
  const t = useTranslations("shell");

  return (
    <Dialog
      open
      title={t("cs2UpdateNoticeTitle")}
      closeLabel={t("cs2UpdateNoticeClose")}
      onClose={onClose}
      className="max-w-2xl"
    >
      <div className="space-y-5">
        <section className="space-y-3 rounded-lg border border-warn/30 bg-warn-muted/30 p-4">
          <h3 className="font-semibold text-fg">
            {t("cs2UpdateNoticeHeading")}
          </h3>
          <p className="text-sm leading-6 text-fg-muted">
            {t("cs2UpdateNoticeBody")}
          </p>
          <dl className="grid gap-2 border-t border-warn/20 pt-3 text-sm sm:grid-cols-2">
            <div>
              <dt className="text-xs text-fg-subtle">
                {t("cs2UpdateNoticeVersionLabel")}
              </dt>
              <dd className="font-mono text-fg">{notice.version}</dd>
            </div>
            <div>
              <dt className="text-xs text-fg-subtle">
                {t("cs2UpdateNoticeDetectedLabel")}
              </dt>
              <dd className="text-fg">
                {format.dateTime(new Date(notice.changedAt), {
                  dateStyle: "medium",
                  timeStyle: "short",
                })}
              </dd>
            </div>
          </dl>
        </section>
        <section className="space-y-2">
          <h3 className="text-sm font-semibold text-fg">
            {t("cs2UpdateNoticeAdviceTitle")}
          </h3>
          <p className="text-sm leading-6 text-fg-muted">
            {t("cs2UpdateNoticeAdvice")}
          </p>
        </section>
      </div>
    </Dialog>
  );
}
