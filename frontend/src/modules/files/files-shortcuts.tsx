"use client";

import { useMemo, useState } from "react";
import { useTranslations } from "next-intl";
import { Pin, X } from "lucide-react";
import { COMMON_FILE_SHORTCUTS, joinUnderRoot, normalizeDir } from "@/modules/files/paths";
import {
  hasShortcutPath,
  shortcutLabelFromPath,
  useCustomShortcuts,
  writeCustomShortcuts,
  type CustomFileShortcut,
} from "@/modules/files/shortcuts";
import { notify } from "@/shared/feedback";
import { Button } from "@/shared/ui/button";
import { Input } from "@/shared/ui/input";
import { cn } from "@/shared/lib/cn";

export function FilesShortcuts({
  serverId,
  root,
  path,
  disabled,
  onGo,
}: {
  serverId: number;
  root: string;
  path: string;
  disabled: boolean;
  onGo: (next: string) => void;
}) {
  const t = useTranslations("files");
  const custom = useCustomShortcuts(serverId);
  const [pinning, setPinning] = useState(false);
  const [pinLabel, setPinLabel] = useState("");
  const current = normalizeDir(path);

  const presets = useMemo(
    () =>
      COMMON_FILE_SHORTCUTS.map((item) => ({
        id: item.id,
        label: t(`shortcut.${item.id}`),
        path: joinUnderRoot(root, item.relative),
      })),
    [root, t],
  );

  function persist(next: CustomFileShortcut[]) {
    writeCustomShortcuts(serverId, next);
  }

  function startPin() {
    setPinLabel(shortcutLabelFromPath(current));
    setPinning(true);
  }

  function savePin() {
    const label = pinLabel.trim() || shortcutLabelFromPath(current);
    if (hasShortcutPath(custom, current)) {
      notify.success(t("shortcutExists"));
      setPinning(false);
      return;
    }
    persist([...custom, { id: crypto.randomUUID(), label, path: current }]);
    setPinning(false);
    notify.success(t("shortcutPinned"));
  }

  return (
    <div className="space-y-2" data-testid="files-shortcuts">
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="mr-1 text-xs text-fg-subtle">{t("shortcuts")}</span>
        {presets.map((item) => (
          <ShortcutChip
            key={item.id}
            testId={`files-shortcut-${item.id}`}
            label={item.label}
            path={item.path}
            active={item.path === current}
            disabled={disabled}
            onClick={() => onGo(item.path)}
          />
        ))}
        {custom.map((item) => (
          <ShortcutChip
            key={item.id}
            testId={`files-shortcut-custom-${item.id}`}
            label={item.label}
            path={item.path}
            active={item.path === current}
            disabled={disabled}
            onClick={() => onGo(item.path)}
            onRemove={() => {
              persist(custom.filter((entry) => entry.id !== item.id));
            }}
            removeLabel={t("unpinShortcut")}
          />
        ))}
        <Button
          type="button"
          size="sm"
          variant="ghost"
          data-testid="files-pin-shortcut"
          disabled={disabled}
          onClick={startPin}
        >
          <Pin />
          {t("pinShortcut")}
        </Button>
      </div>
      {pinning ? (
        <form
          className="flex flex-wrap items-end gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            event.stopPropagation();
            savePin();
          }}
        >
          <Input
            className="max-w-56"
            value={pinLabel}
            autoFocus
            aria-label={t("pinShortcutName")}
            placeholder={t("pinShortcutName")}
            onChange={(event) => setPinLabel(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Escape") {
                event.preventDefault();
                setPinning(false);
              }
            }}
          />
          <Button type="submit" size="sm">
            {t("pinShortcutSave")}
          </Button>
          <Button type="button" size="sm" variant="ghost" onClick={() => setPinning(false)}>
            {t("cancel")}
          </Button>
        </form>
      ) : null}
    </div>
  );
}

function ShortcutChip({
  label,
  path,
  active,
  disabled,
  onClick,
  onRemove,
  removeLabel,
  testId,
}: {
  label: string;
  path?: string;
  active: boolean;
  disabled: boolean;
  onClick: () => void;
  onRemove?: () => void;
  removeLabel?: string;
  testId: string;
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full border text-xs",
        active
          ? "border-primary/40 bg-primary-muted text-primary"
          : "border-line bg-surface-overlay text-fg-muted",
      )}
    >
      <button
        type="button"
        data-testid={testId}
        title={path}
        disabled={disabled}
        className="px-2.5 py-1 font-medium hover:text-fg disabled:opacity-50"
        onClick={onClick}
      >
        {label}
      </button>
      {onRemove ? (
        <button
          type="button"
          className="pr-1.5 text-fg-subtle hover:text-fg"
          aria-label={removeLabel}
          disabled={disabled}
          onClick={onRemove}
        >
          <X className="size-3" />
        </button>
      ) : null}
    </span>
  );
}
