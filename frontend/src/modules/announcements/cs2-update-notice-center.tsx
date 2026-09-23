"use client";

import { useEffect, useState } from "react";
import dynamic from "next/dynamic";
import { useTranslations } from "next-intl";
import type { CS2UpdateNotice } from "@/modules/announcements/types";
import { Button } from "@/shared/ui/button";

const CS2UpdateNoticeDialog = dynamic(
  () =>
    import("@/modules/announcements/announcement-content").then(
      (module) => module.CS2UpdateNoticeDialog,
    ),
  { loading: () => null },
);

export function CS2UpdateNoticeCenter({
  notice,
}: {
  notice: CS2UpdateNotice;
}) {
  const t = useTranslations("shell");
  const [open, setOpen] = useState(false);
  const [expiredNoticeAt, setExpiredNoticeAt] = useState<string | null>(null);
  const expired = notice.expiresAt === expiredNoticeAt;

  useEffect(() => {
    const remaining = Date.parse(notice.expiresAt) - Date.now();
    const timer = window.setTimeout(
      () => setExpiredNoticeAt(notice.expiresAt),
      Math.max(0, remaining),
    );
    return () => window.clearTimeout(timer);
  }, [notice.expiresAt]);

  if (expired) return null;

  return (
    <>
      <Button
        type="button"
        variant="outline"
        size="sm"
        onClick={() => setOpen(true)}
        aria-label={t("cs2UpdateNoticeButton")}
        title={t("cs2UpdateNoticeButton")}
        className="gap-1.5 border-warn/40 bg-warn-muted/50 px-2 text-warn hover:bg-warn-muted sm:px-3"
      >
        <span
          aria-hidden="true"
          className="inline-flex size-4 items-center justify-center rounded-full border border-current text-[10px] font-bold leading-none"
        >
          !
        </span>
        <span className="hidden sm:inline">{t("cs2UpdateNoticeButton")}</span>
      </Button>
      {open ? (
        <CS2UpdateNoticeDialog
          notice={notice}
          onClose={() => setOpen(false)}
        />
      ) : null}
    </>
  );
}
