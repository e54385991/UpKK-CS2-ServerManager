"use client";

import { useEffect, useSyncExternalStore } from "react";
import { createPortal } from "react-dom";
import { useTranslations } from "next-intl";
import { Button } from "@/shared/ui/button";
import {
  getConfirmServerSnapshot,
  getConfirmSnapshot,
  resolveConfirm,
  subscribeConfirm,
} from "@/shared/feedback/confirm-store";

export function ConfirmHost() {
  const t = useTranslations("feedback");
  const request = useSyncExternalStore(
    subscribeConfirm,
    getConfirmSnapshot,
    getConfirmServerSnapshot,
  );

  useEffect(() => {
    if (!request) return;
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") resolveConfirm(false);
    }
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    document.addEventListener("keydown", onKey);
    return () => {
      document.body.style.overflow = previous;
      document.removeEventListener("keydown", onKey);
    };
  }, [request]);

  if (!request || typeof document === "undefined") return null;

  const { options } = request;
  const title = options.title ?? t("title");
  const description = options.description;
  const tone = options.tone ?? "danger";

  return createPortal(
    <div className="fixed inset-0 z-[60] flex items-end justify-center p-4 sm:items-center">
      <button
        type="button"
        className="absolute inset-0 bg-black/65"
        aria-label={t("close")}
        onClick={() => resolveConfirm(false)}
      />
      <div
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="app-confirm-title"
        aria-describedby={description ? "app-confirm-desc" : undefined}
        data-testid="app-confirm"
        className="relative z-10 w-full max-w-md rounded-xl border border-line bg-surface shadow-panel"
      >
        <div className="space-y-2 px-5 py-4">
          <h2 id="app-confirm-title" className="text-base font-semibold text-fg">
            {title}
          </h2>
          {description ? (
            <p
              id="app-confirm-desc"
              className="whitespace-pre-wrap text-sm text-fg-muted"
            >
              {description}
            </p>
          ) : null}
        </div>
        <div className="flex justify-end gap-2 border-t border-line px-5 py-3">
          <Button
            type="button"
            variant="secondary"
            autoFocus={tone === "danger"}
            onClick={() => resolveConfirm(false)}
          >
            {options.cancelLabel ?? t("cancel")}
          </Button>
          <Button
            type="button"
            variant={tone === "danger" ? "danger" : "primary"}
            autoFocus={tone !== "danger"}
            onClick={() => resolveConfirm(true)}
          >
            {options.confirmLabel ?? t("confirm")}
          </Button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
