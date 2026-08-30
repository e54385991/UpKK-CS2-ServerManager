"use client";

import { useCallback, useEffect, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import dynamic from "next/dynamic";
import { useTranslations } from "next-intl";
import { LoaderCircle, X } from "lucide-react";
import { editorLanguageId } from "@/modules/files/language";
import { confirm } from "@/shared/feedback";
import { Button } from "@/shared/ui/button";
import { cn } from "@/shared/lib/cn";

const FileCodeEditor = dynamic(
  () => import("@/modules/files/file-code-editor").then((mod) => mod.FileCodeEditor),
  { ssr: false },
);

export type EditorFile = {
  readonly path: string;
  readonly name: string;
  readonly content: string;
  readonly loading?: boolean;
};

export function FileEditorDialog({
  file,
  busy,
  onClose,
  onSave,
}: {
  file: EditorFile;
  busy: boolean;
  onClose: () => void;
  onSave: (content: string) => Promise<boolean>;
}) {
  if (typeof document === "undefined") return null;
  if (file.loading) {
    return createPortal(
      <EditorFrame file={file} dirty={false} busy={busy} onClose={onClose}>
        <div className="flex h-full items-center justify-center gap-2 text-sm text-fg-subtle">
          <LoaderCircle className="size-4 animate-spin" />
          <EditorLoadingLabel />
        </div>
      </EditorFrame>,
      document.body,
    );
  }
  return createPortal(
    <LiveEditor key={file.path} file={file} busy={busy} onClose={onClose} onSave={onSave} />,
    document.body,
  );
}

function EditorLoadingLabel() {
  const t = useTranslations("files");
  return t("editLoading");
}

function LiveEditor({
  file,
  busy,
  onClose,
  onSave,
}: {
  file: EditorFile;
  busy: boolean;
  onClose: () => void;
  onSave: (content: string) => Promise<boolean>;
}) {
  const [draft, setDraft] = useState(file.content);
  const dirty = draft !== file.content;

  const save = useCallback(async () => {
    if (busy) return;
    const ok = await onSave(draft);
    if (ok) onClose();
  }, [busy, draft, onClose, onSave]);

  return (
    <EditorFrame
      file={file}
      dirty={dirty}
      busy={busy}
      onClose={onClose}
      onSave={() => void save()}
    >
      <FileCodeEditor
        value={draft}
        fileName={file.name}
        readOnly={busy}
        onChange={setDraft}
      />
    </EditorFrame>
  );
}

function EditorFrame({
  file,
  dirty,
  busy,
  children,
  onClose,
  onSave,
}: {
  file: EditorFile;
  dirty: boolean;
  busy: boolean;
  children: ReactNode;
  onClose: () => void;
  onSave?: () => void;
}) {
  const t = useTranslations("files");
  const language = editorLanguageId(file.name) || t("editPlaintext");

  const requestClose = useCallback(async () => {
    if (busy) return;
    if (dirty) {
      const discard = await confirm({
        title: t("editUnsavedTitle"),
        description: t("editUnsaved"),
        confirmLabel: t("editDiscard"),
        cancelLabel: t("cancel"),
        tone: "default",
      });
      if (!discard) return;
    }
    onClose();
  }, [busy, dirty, onClose, t]);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "s") {
        event.preventDefault();
        onSave?.();
        return;
      }
      if (event.key !== "Escape") return;
      if (event.target instanceof HTMLElement && event.target.closest(".cm-panel")) {
        return;
      }
      event.preventDefault();
      void requestClose();
    }
    window.addEventListener("keydown", onKey);
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = previous;
      window.removeEventListener("keydown", onKey);
    };
  }, [onSave, requestClose]);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-3 sm:p-6">
      <button
        type="button"
        className="absolute inset-0 bg-black/70"
        aria-label={t("cancel")}
        onClick={() => void requestClose()}
      />
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="file-editor-title"
        data-testid="files-editor-dialog"
        className="relative z-10 flex h-[min(92dvh,880px)] w-[min(96vw,1280px)] flex-col overflow-hidden rounded-xl border border-line bg-[#1e1e1e] shadow-panel"
      >
        <header className="flex items-start justify-between gap-3 border-b border-[#2b2b2b] px-4 py-3">
          <div className="min-w-0 space-y-1">
            <div className="flex items-center gap-2">
              <h2 id="file-editor-title" className="truncate font-mono text-sm font-semibold text-fg">
                {file.name}
              </h2>
              {dirty ? (
                <span className="rounded-full bg-warn-muted px-2 py-0.5 text-[11px] text-warn">
                  {t("editDirty")}
                </span>
              ) : null}
            </div>
            <p className="truncate font-mono text-xs text-fg-subtle">{file.path}</p>
          </div>
          <Button
            type="button"
            variant="ghost"
            size="icon"
            onClick={() => void requestClose()}
            aria-label={t("cancel")}
          >
            <X />
          </Button>
        </header>
        <div className="min-h-0 flex-1 bg-[#1e1e1e]">{children}</div>
        <footer className="flex flex-wrap items-center justify-between gap-3 border-t border-[#2b2b2b] px-4 py-3">
          <p className={cn("font-mono text-xs text-fg-subtle")}>
            {language}
            <span className="mx-2 text-fg-subtle/50">·</span>
            {t("editHint")}
          </p>
          <div className="flex gap-2">
            <Button type="button" variant="secondary" disabled={busy} onClick={() => void requestClose()}>
              {t("cancel")}
            </Button>
            <Button
              type="button"
              data-testid="files-editor-save"
              disabled={busy || file.loading || !dirty}
              onClick={() => onSave?.()}
            >
              {busy ? t("saving") : t("saveFile")}
            </Button>
          </div>
        </footer>
      </div>
    </div>
  );
}
