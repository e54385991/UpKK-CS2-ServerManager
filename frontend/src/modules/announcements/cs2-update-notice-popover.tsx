import { getFormatter, getTranslations } from "next-intl/server";
import type { CS2UpdateNotice } from "@/modules/announcements/types";

const POPOVER_ID = "cs2-update-notice";

export async function CS2UpdateNoticePopover({
  notice,
}: {
  notice: CS2UpdateNotice;
}) {
  const [t, format] = await Promise.all([
    getTranslations("shell"),
    getFormatter(),
  ]);

  return (
    <>
      <button
        type="button"
        popoverTarget={POPOVER_ID}
        aria-haspopup="dialog"
        className="inline-flex h-8 items-center gap-1.5 rounded-md border border-warn/40 bg-warn-muted/50 px-2 text-sm text-warn transition-colors hover:bg-warn-muted sm:px-3"
      >
        <span
          aria-hidden="true"
          className="inline-flex size-4 items-center justify-center rounded-full border border-current text-[10px] font-bold leading-none"
        >
          !
        </span>
        <span>{t("cs2UpdateNoticeButton")}</span>
      </button>
      <dialog
        id={POPOVER_ID}
        popover="auto"
        aria-labelledby={`${POPOVER_ID}-title`}
        className="fixed inset-0 m-auto max-h-[min(85vh,48rem)] w-[calc(100%-2rem)] max-w-2xl overflow-auto rounded-xl border border-line bg-surface p-5 text-left text-fg shadow-panel backdrop:bg-black/50 sm:p-6"
      >
        <div className="space-y-5">
          <header className="flex items-start justify-between gap-4">
            <h2
              id={`${POPOVER_ID}-title`}
              className="text-lg font-semibold text-fg"
            >
              {t("cs2UpdateNoticeTitle")}
            </h2>
            <button
              type="button"
              popoverTarget={POPOVER_ID}
              popoverTargetAction="hide"
              aria-label={t("cs2UpdateNoticeClose")}
              className="rounded-md px-2 py-1 text-fg-subtle hover:bg-surface-raised hover:text-fg"
            >
              ×
            </button>
          </header>
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
      </dialog>
    </>
  );
}
