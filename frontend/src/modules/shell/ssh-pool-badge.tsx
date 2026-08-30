"use client";

import { useEffect, useState } from "react";
import { useTranslations } from "next-intl";
import { refreshSshPoolAction } from "@/modules/shell/actions";
import type { SshPoolStats } from "@/modules/shell/types";
import { StatusDot } from "@/shared/ui/badge";

const POLL_MS = 15_000;

function toneFor(stats: SshPoolStats | null): "ok" | "info" | "neutral" {
  if (!stats) return "neutral";
  if (stats.leases > 0) return "info";
  if (stats.connections > 0) return "ok";
  return "neutral";
}

export function SshPoolBadge() {
  const t = useTranslations("shell");
  const [stats, setStats] = useState<SshPoolStats | null>(null);

  useEffect(() => {
    let cancelled = false;
    let timer = 0;

    async function load() {
      if (document.hidden) return;
      const result = await refreshSshPoolAction();
      if (!cancelled && result.ok) setStats(result.data);
    }

    const schedule = () => {
      window.clearInterval(timer);
      if (document.hidden) return;
      void load();
      timer = window.setInterval(() => {
        void load();
      }, POLL_MS);
    };

    document.addEventListener("visibilitychange", schedule);
    schedule();
    return () => {
      cancelled = true;
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", schedule);
    };
  }, []);

  const connections = stats?.connections ?? 0;
  const leases = stats?.leases ?? 0;
  const idle = stats?.idle ?? 0;
  const label =
    stats && leases > 0
      ? t("sshPoolBusy", { count: connections, leases })
      : t("sshPool", { count: connections });

  return (
    <span
      className="inline-flex items-center gap-1.5 text-xs text-fg-subtle"
      title={
        stats
          ? t("sshPoolTitle", { count: connections, leases, idle })
          : t("sshPoolLoading")
      }
    >
      <StatusDot tone={toneFor(stats)} pulse={leases > 0} />
      <span className="tabular-nums">{label}</span>
    </span>
  );
}
