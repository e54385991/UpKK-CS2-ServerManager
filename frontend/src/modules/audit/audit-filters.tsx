"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, type FormEvent } from "react";
import { useTranslations } from "next-intl";
import type { Route } from "next";
import { Search } from "lucide-react";
import {
  AUDIT_CATEGORY_VALUES,
  AUDIT_STATUS_VALUES,
} from "@/modules/audit/types";
import { Button } from "@/shared/ui/button";
import { Input } from "@/shared/ui/input";
import { cn } from "@/shared/lib/cn";

const selectClass = cn(
  "h-9 rounded-md border border-line bg-surface px-3 text-sm text-fg outline-none",
  "transition-colors focus-visible:border-primary/60 focus-visible:ring-2 focus-visible:ring-primary/40",
);

export function AuditFilters() {
  const router = useRouter();
  const params = useSearchParams();
  const t = useTranslations("audit");

  const apply = useCallback(
    (form: HTMLFormElement) => {
      const data = new FormData(form);
      const next = new URLSearchParams();
      const q = String(data.get("q") ?? "").trim();
      const category = String(data.get("category") ?? "").trim();
      const status = String(data.get("status") ?? "").trim();
      if (q) next.set("q", q);
      if (category) next.set("category", category);
      if (status) next.set("status", status);
      const query = next.toString();
      router.replace((query ? `/audit?${query}` : "/audit") as Route);
    },
    [router],
  );

  const onSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    apply(event.currentTarget);
  };

  return (
    <form
      key={params.toString()}
      onSubmit={onSubmit}
      className="flex flex-wrap items-center gap-2"
    >
      <Input
        name="q"
        defaultValue={params.get("q") ?? ""}
        placeholder={t("searchPlaceholder")}
        aria-label={t("search")}
        data-testid="audit-search"
        maxLength={100}
        className="h-9 w-56"
      />
      <select
        name="category"
        aria-label={t("filterCategory")}
        className={selectClass}
        defaultValue={params.get("category") ?? ""}
        onChange={(event) => {
          const form = event.currentTarget.form;
          if (form) apply(form);
        }}
      >
        <option value="">{t("allCategories")}</option>
        {AUDIT_CATEGORY_VALUES.map((value) => (
          <option key={value} value={value}>
            {t(`categories.${value}`)}
          </option>
        ))}
      </select>

      <select
        name="status"
        aria-label={t("filterStatus")}
        className={selectClass}
        defaultValue={params.get("status") ?? ""}
        onChange={(event) => {
          const form = event.currentTarget.form;
          if (form) apply(form);
        }}
      >
        <option value="">{t("allStatuses")}</option>
        {AUDIT_STATUS_VALUES.map((value) => (
          <option key={value} value={value}>
            {t(`statuses.${value}`)}
          </option>
        ))}
      </select>
      <Button type="submit" variant="secondary" size="sm">
        <Search className="size-4" />
        {t("search")}
      </Button>
    </form>
  );
}
