"use client";

import { useState, useTransition } from "react";
import { useTranslations } from "next-intl";
import { RefreshCw } from "lucide-react";
import {
  refreshDiskSpaceAction,
  refreshServerDiskSpaceAction,
} from "@/modules/servers/actions";
import type { ServerListScope } from "@/modules/servers/types";
import { Button } from "@/shared/ui/button";

export function DiskSpaceRefreshButton({ scope }: { scope: ServerListScope }) {
  const t = useTranslations("servers");
  const [pending, start] = useTransition();
  const [error, setError] = useState<string | null>(null);

  return (
    <div className="flex flex-wrap items-center gap-2">
      <Button
        type="button"
        variant="outline"
        size="sm"
        disabled={pending}
        onClick={() => {
          start(async () => {
            const result = await refreshDiskSpaceAction(scope);
            setError(result.ok ? null : t("diskSpace.refreshFailed"));
          });
        }}
      >
        <RefreshCw className={pending ? "size-3.5 animate-spin" : "size-3.5"} />
        {pending ? t("diskSpace.refreshing") : t("diskSpace.refresh")}
      </Button>
      {error ? <span className="text-xs text-danger">{error}</span> : null}
    </div>
  );
}

export function ServerDiskRefreshButton({ serverId }: { serverId: number }) {
  const t = useTranslations("servers");
  const [pending, start] = useTransition();

  return (
    <button
      type="button"
      disabled={pending}
      aria-label={t("diskSpace.refreshOne")}
      className="inline-flex size-6 items-center justify-center rounded-md text-fg-subtle transition-colors hover:bg-surface-overlay hover:text-fg disabled:opacity-50"
      onClick={() => {
        start(async () => {
          await refreshServerDiskSpaceAction(serverId);
        });
      }}
    >
      <RefreshCw className={pending ? "size-3 animate-spin" : "size-3"} />
    </button>
  );
}
