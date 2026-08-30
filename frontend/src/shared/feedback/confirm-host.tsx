"use client";

import { useEffect, useState, useSyncExternalStore } from "react";
import { createPortal } from "react-dom";
import { useTranslations } from "next-intl";
import { Button } from "@/shared/ui/button";
import { Input, Label } from "@/shared/ui/input";
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

  return createPortal(
    <ConfirmDialog
      key={request.id}
      title={request.options.title ?? t("title")}
      description={request.options.description}
      confirmLabel={request.options.confirmLabel ?? t("confirm")}
      cancelLabel={request.options.cancelLabel ?? t("cancel")}
      closeLabel={t("close")}
      challengeLabel={request.options.challengeLabel ?? t("challenge")}
      challenge={request.options.challenge}
      tone={request.options.tone ?? "danger"}
    />,
    document.body,
  );
}

function ConfirmDialog({
  title,
  description,
  confirmLabel,
  cancelLabel,
  closeLabel,
  challengeLabel,
  challenge,
  tone,
}: {
  title: string;
  description?: string;
  confirmLabel: string;
  cancelLabel: string;
  closeLabel: string;
  challengeLabel: string;
  challenge?: string;
  tone: "danger" | "default";
}) {
  const [typed, setTyped] = useState("");
  const ready = !challenge || typed.trim() === challenge;

  return (
    <div className="fixed inset-0 z-[60] flex items-end justify-center p-4 sm:items-center">
      <button
        type="button"
        className="absolute inset-0 bg-black/65"
        aria-label={closeLabel}
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
        <div className="space-y-3 px-5 py-4">
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
          {challenge ? (
            <div>
              <Label htmlFor="app-confirm-challenge">{challengeLabel}</Label>
              <Input
                id="app-confirm-challenge"
                data-testid="app-confirm-challenge"
                value={typed}
                autoFocus
                inputMode="numeric"
                autoComplete="off"
                spellCheck={false}
                onChange={(event) => setTyped(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && ready) {
                    event.preventDefault();
                    resolveConfirm(true);
                  }
                }}
              />
            </div>
          ) : null}
        </div>
        <div className="flex justify-end gap-2 border-t border-line px-5 py-3">
          <Button
            type="button"
            variant="secondary"
            autoFocus={!challenge && tone === "danger"}
            onClick={() => resolveConfirm(false)}
          >
            {cancelLabel}
          </Button>
          <Button
            type="button"
            variant={tone === "danger" ? "danger" : "primary"}
            disabled={!ready}
            autoFocus={!challenge && tone !== "danger"}
            onClick={() => resolveConfirm(true)}
          >
            {confirmLabel}
          </Button>
        </div>
      </div>
    </div>
  );
}
