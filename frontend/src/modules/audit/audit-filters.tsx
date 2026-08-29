"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, type ChangeEvent } from "react";
import { AUDIT_CATEGORIES, AUDIT_STATUSES } from "@/modules/audit/types";
import { cn } from "@/shared/lib/cn";

const selectClass = cn(
  "h-9 rounded-md border border-line bg-surface px-3 text-sm text-fg outline-none",
  "transition-colors focus-visible:border-primary/60 focus-visible:ring-2 focus-visible:ring-primary/40",
);

export function AuditFilters() {
  const router = useRouter();
  const params = useSearchParams();

  const update = useCallback(
    (key: string, value: string) => {
      const next = new URLSearchParams(params.toString());
      if (value) next.set(key, value);
      else next.delete(key);
      // Any filter change resets pagination.
      next.delete("offset");
      router.replace(`/audit?${next.toString()}`);
    },
    [params, router],
  );

  const onCategory = (event: ChangeEvent<HTMLSelectElement>) =>
    update("category", event.target.value);
  const onStatus = (event: ChangeEvent<HTMLSelectElement>) =>
    update("status", event.target.value);

  return (
    <div className="flex flex-wrap items-center gap-3">
      <select
        aria-label="按分类筛选"
        className={selectClass}
        value={params.get("category") ?? ""}
        onChange={onCategory}
      >
        <option value="">全部分类</option>
        {AUDIT_CATEGORIES.map((c) => (
          <option key={c.value} value={c.value}>
            {c.label}
          </option>
        ))}
      </select>

      <select
        aria-label="按状态筛选"
        className={selectClass}
        value={params.get("status") ?? ""}
        onChange={onStatus}
      >
        <option value="">全部状态</option>
        {AUDIT_STATUSES.map((s) => (
          <option key={s.value} value={s.value}>
            {s.label}
          </option>
        ))}
      </select>
    </div>
  );
}
