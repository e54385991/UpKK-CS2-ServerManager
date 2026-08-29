"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, type ChangeEvent } from "react";
import { useTranslations } from "next-intl";
import {
  AUDIT_CATEGORY_VALUES,
  AUDIT_STATUS_VALUES,
} from "@/modules/audit/types";
import { cn } from "@/shared/lib/cn";

const selectClass = cn(
  "h-9 rounded-md border border-line bg-surface px-3 text-sm text-fg outline-none",
  "transition-colors focus-visible:border-primary/60 focus-visible:ring-2 focus-visible:ring-primary/40",
);

export function AuditFilters() {
  const router = useRouter();
  const params = useSearchParams();
  const t = useTranslations("audit");

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
        aria-label={t("filterCategory")}
        className={selectClass}
        value={params.get("category") ?? ""}
        onChange={onCategory}
      >
        <option value="">{t("allCategories")}</option>
        {AUDIT_CATEGORY_VALUES.map((value) => (
          <option key={value} value={value}>
            {t(`categories.${value}`)}
          </option>
        ))}
      </select>

      <select
        aria-label={t("filterStatus")}
        className={selectClass}
        value={params.get("status") ?? ""}
        onChange={onStatus}
      >
        <option value="">{t("allStatuses")}</option>
        {AUDIT_STATUS_VALUES.map((value) => (
          <option key={value} value={value}>
            {t(`statuses.${value}`)}
          </option>
        ))}
      </select>
    </div>
  );
}
