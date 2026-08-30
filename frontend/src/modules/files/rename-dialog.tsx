"use client";

import { useState } from "react";
import { useTranslations } from "next-intl";
import { renameSelectionEnd } from "@/modules/files/paths";
import type { FileEntry } from "@/modules/files/types";
import { Button } from "@/shared/ui/button";
import { Dialog } from "@/shared/ui/dialog";
import { Input, Label } from "@/shared/ui/input";

export function RenameDialog({
  entry,
  busy,
  onClose,
  onSubmit,
}: {
  entry: FileEntry;
  busy: boolean;
  onClose: () => void;
  onSubmit: (name: string) => Promise<boolean>;
}) {
  return (
    <RenameForm
      key={entry.path}
      entry={entry}
      busy={busy}
      onClose={onClose}
      onSubmit={onSubmit}
    />
  );
}

function RenameForm({
  entry,
  busy,
  onClose,
  onSubmit,
}: {
  entry: FileEntry;
  busy: boolean;
  onClose: () => void;
  onSubmit: (name: string) => Promise<boolean>;
}) {
  const t = useTranslations("files");
  const [draft, setDraft] = useState(entry.name);

  async function submit() {
    const name = draft.trim();
    if (!name || busy || name === entry.name) return;
    const ok = await onSubmit(name);
    if (ok) onClose();
  }

  return (
    <Dialog
      open
      title={t("rename")}
      description={entry.path}
      closeLabel={t("cancel")}
      className="max-w-lg"
      onClose={() => {
        if (!busy) onClose();
      }}
      footer={
        <div className="flex justify-end gap-2">
          <Button type="button" variant="secondary" disabled={busy} onClick={onClose}>
            {t("cancel")}
          </Button>
          <Button
            type="submit"
            form="files-rename-form"
            data-testid="files-rename-save"
            disabled={busy || !draft.trim() || draft.trim() === entry.name}
          >
            {busy ? t("saving") : t("saveRename")}
          </Button>
        </div>
      }
    >
      <form
        id="files-rename-form"
        className="space-y-2"
        onSubmit={(event) => {
          event.preventDefault();
          void submit();
        }}
      >
        <Label htmlFor="files-rename-to">{t("newName")}</Label>
        <Input
          id="files-rename-to"
          data-testid="files-rename-input"
          value={draft}
          disabled={busy}
          spellCheck={false}
          autoComplete="off"
          autoFocus
          onFocus={(event) => {
            event.currentTarget.setSelectionRange(
              0,
              renameSelectionEnd(entry.name, entry.type === "directory"),
            );
          }}
          onChange={(event) => setDraft(event.target.value)}
        />
      </form>
    </Dialog>
  );
}
