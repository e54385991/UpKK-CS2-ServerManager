"use client";

import { useMemo, useState } from "react";
import { useFormatter, useTranslations } from "next-intl";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { formatOperationClock } from "@/modules/servers/operation-live-log";
import { isServerOperationAction, type DeploymentLogEntry } from "@/modules/servers/types";
import { Button } from "@/shared/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/shared/ui/card";

/** Entries per page. Keeps the card short next to the live log. */
export const HISTORY_PAGE_SIZE = 5;

export function pageCount(total: number, pageSize = HISTORY_PAGE_SIZE): number {
  return Math.max(1, Math.ceil(total / pageSize));
}

export function pageSlice<T>(
  items: readonly T[],
  page: number,
  pageSize = HISTORY_PAGE_SIZE,
): readonly T[] {
  const clamped = Math.min(Math.max(page, 0), pageCount(items.length, pageSize) - 1);
  const start = clamped * pageSize;
  return items.slice(start, start + pageSize);
}

export function OperationHistory({ logs }: { logs: readonly DeploymentLogEntry[] }) {
  const t = useTranslations("serverDetail");
  const format = useFormatter();
  const [page, setPage] = useState(0);

  const total = logs.length;
  const pages = pageCount(total);
  // A finished operation prepends an entry, so clamp instead of trusting state.
  const current = Math.min(page, pages - 1);
  const visible = useMemo(() => pageSlice(logs, current), [logs, current]);

  return (
    <Card>
      <CardHeader>
        <div>
          <CardTitle>{t("history")}</CardTitle>
          <CardDescription>{t("historyHelp")}</CardDescription>
        </div>
        {total > HISTORY_PAGE_SIZE ? (
          <div className="flex items-center gap-1.5">
            <Button
              type="button"
              variant="ghost"
              size="sm"
              aria-label={t("historyPrevious")}
              disabled={current === 0}
              onClick={() => setPage(current - 1)}
            >
              <ChevronLeft className="size-4" />
            </Button>
            <span className="font-mono text-xs text-fg-subtle">
              {t("historyPage", { page: current + 1, pages })}
            </span>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              aria-label={t("historyNext")}
              disabled={current >= pages - 1}
              onClick={() => setPage(current + 1)}
            >
              <ChevronRight className="size-4" />
            </Button>
          </div>
        ) : null}
      </CardHeader>
      <CardContent className="p-0">
        {total === 0 ? (
          <p className="px-5 py-8 text-sm text-fg-subtle">{t("historyEmpty")}</p>
        ) : (
          <ul className="divide-y divide-line">
            {visible.map((entry) => (
              <li key={entry.id} className="px-5 py-3">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <p className="text-sm font-medium text-fg">
                    {isServerOperationAction(entry.action)
                      ? t(`actions.${entry.action}`)
                      : entry.action}
                  </p>
                  <span className="font-mono text-xs text-fg-subtle">
                    {entry.createdAt
                      ? formatOperationClock(entry.createdAt, format.dateTime)
                      : "—"}
                  </span>
                </div>
                <p className="mt-1 text-xs text-fg-muted">
                  {entry.status}
                  {entry.errorMessage ? ` · ${entry.errorMessage}` : ""}
                </p>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}
