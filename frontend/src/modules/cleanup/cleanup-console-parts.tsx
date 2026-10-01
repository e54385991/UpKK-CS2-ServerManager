"use client";

import {
type CleanupItem
} from "@/modules/cleanup/types";
import { Button } from "@/shared/ui/button";
import { Skeleton } from "@/shared/ui/skeleton";
import { useState } from "react";

export function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function ItemList({ items, limit = 20 }: { items: readonly CleanupItem[]; limit?: number }) {
  const shown = items.slice(0, limit);
  return (
    <ul className="max-h-40 space-y-1 overflow-auto text-xs text-fg-muted">
      {shown.map((item) => (
        <li key={item.path} className="break-all">
          {item.path} · {formatSize(item.size)}
        </li>
      ))}
      {items.length > limit ? <li>…</li> : null}
    </ul>
  );
}

export function CommandBlock({
  title,
  lines,
  copyLabel,
  copiedLabel,
}: {
  title: string;
  lines: readonly string[];
  copyLabel: string;
  copiedLabel: string;
}) {
  const [copied, setCopied] = useState(false);
  if (lines.length === 0) return null;
  return (
    <div className="space-y-2 rounded-md border border-line bg-surface-raised p-3">
      <div className="flex items-center justify-between gap-2">
        <p className="text-xs font-medium text-fg">{title}</p>
        <Button
          type="button"
          size="sm"
          variant="outline"
          onClick={() => {
            void navigator.clipboard.writeText(lines.join("\n")).then(() => {
              setCopied(true);
              window.setTimeout(() => setCopied(false), 1500);
            });
          }}
        >
          {copied ? copiedLabel : copyLabel}
        </Button>
      </div>
      <pre className="max-h-40 overflow-auto whitespace-pre-wrap break-all font-mono text-[11px] text-fg-muted">
        {lines.join("\n")}
      </pre>
    </div>
  );
}

export function CleanupPanelSkeleton() {
  return (
    <div className="max-w-5xl rounded-lg border border-line bg-surface p-5 shadow-panel">
      <Skeleton className="mb-4 h-4 w-40" />
      <Skeleton className="mb-2 h-4 w-72" />
      <Skeleton className="h-48 w-full" />
    </div>
  );
}

