"use client";

import { LoaderCircle } from "lucide-react";
import { APT_MIRRORS, type AptMirrorId } from "@/modules/servers/apt-mirrors";
import { Button } from "@/shared/ui/button";

export function AptMirrorSwitcher({
  current,
  disabled,
  busyMirror,
  onSelect,
  labelFor,
  applyLabel,
}: {
  current: AptMirrorId | null;
  disabled: boolean;
  busyMirror: AptMirrorId | null;
  onSelect: (mirror: AptMirrorId) => void;
  labelFor: (mirror: AptMirrorId) => string;
  applyLabel: string;
}) {
  return (
    <div className="flex flex-wrap gap-2">
      {APT_MIRRORS.map((mirror) => (
        <Button
          key={mirror}
          type="button"
          size="sm"
          variant={current === mirror ? "primary" : "outline"}
          disabled={disabled}
          onClick={() => onSelect(mirror)}
        >
          {busyMirror === mirror ? <LoaderCircle className="animate-spin" /> : null}
          {labelFor(mirror)}
          <span className="sr-only">{applyLabel}</span>
        </Button>
      ))}
    </div>
  );
}
