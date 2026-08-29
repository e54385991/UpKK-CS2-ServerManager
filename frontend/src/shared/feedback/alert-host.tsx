"use client";

import { useEffect, useSyncExternalStore } from "react";
import { createPortal } from "react-dom";
import { useTranslations } from "next-intl";
import { Button } from "@/shared/ui/button";
import {
  getAlertServerSnapshot,
  getAlertSnapshot,
  resolveAlert,
  subscribeAlert,
} from "@/shared/feedback/alert-store";

export function AlertHost() {
  const t = useTranslations("feedback");
  const request = useSyncExternalStore(
    subscribeAlert,
    getAlertSnapshot,
    getAlertServerSnapshot,
  );

  useEffect(() => {
    if (!request) return;
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") resolveAlert();
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
  const title = options.title ?? t("alertTitle");
  const description = options.description;
  const tone = options.tone ?? "danger";

  return createPortal(
    <div className="fixed inset-0 z-[60] flex items-end justify-center p-4 sm:items-center">
      <button
        type="button"
        className="absolute inset-0 bg-black/65"
        aria-label={t("close")}
        onClick={() => resolveAlert()}
      />
      <div
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="app-alert-title"
        aria-describedby={description ? "app-alert-desc" : undefined}
        data-testid="app-alert"
        className={
          tone === "ok"
            ? "relative z-10 w-full max-w-md rounded-xl border border-ok/40 bg-surface shadow-panel"
            : tone === "warn"
              ? "relative z-10 w-full max-w-md rounded-xl border border-warn/40 bg-surface shadow-panel"
              : "relative z-10 w-full max-w-md rounded-xl border border-line bg-surface shadow-panel"
        }
      >
        <div className="space-y-2 px-5 py-4">
          <h2
            id="app-alert-title"
            data-testid="app-alert-title"
            className={
              tone === "ok"
                ? "text-base font-semibold text-ok"
                : tone === "warn"
                  ? "text-base font-semibold text-warn"
                  : tone === "danger"
                    ? "text-base font-semibold text-danger"
                    : "text-base font-semibold text-fg"
            }
          >
            {title}
          </h2>
          {description ? (
            <p
              id="app-alert-desc"
              className="whitespace-pre-wrap text-sm text-fg-muted"
            >
              {description}
            </p>
          ) : null}
        </div>
        <div className="flex justify-end border-t border-line px-5 py-3">
          <Button
            type="button"
            variant={tone === "danger" ? "danger" : "primary"}
            autoFocus
            onClick={() => resolveAlert()}
          >
            {options.confirmLabel ?? t("ok")}
          </Button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
