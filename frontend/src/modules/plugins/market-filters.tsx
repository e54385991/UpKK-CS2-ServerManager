"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, type FormEvent } from "react";
import { useTranslations } from "next-intl";
import type { Route } from "next";
import { Search } from "lucide-react";
import { MARKET_SORTS, PLUGIN_CATEGORIES } from "@/modules/plugins/types";
import { Button } from "@/shared/ui/button";
import { Input } from "@/shared/ui/input";
import { Select } from "@/shared/ui/select";

export function MarketFilters() {
  const router = useRouter();
  const params = useSearchParams();
  const t = useTranslations("plugins");

  const onSubmit = useCallback(
    (event: FormEvent<HTMLFormElement>) => {
      event.preventDefault();
      const form = new FormData(event.currentTarget);
      const next = new URLSearchParams();
      const q = String(form.get("q") ?? "").trim();
      const category = String(form.get("category") ?? "").trim();
      const sort = String(form.get("sort") ?? "").trim();
      const serverId = params.get("serverId");
      // Keep the active marketplace section; searching must not jump tabs.
      const framework = params.get("framework");
      if (q) next.set("q", q);
      if (category) next.set("category", category);
      if (sort && sort !== "recommended") next.set("sort", sort);
      if (framework) next.set("framework", framework);
      if (serverId) next.set("serverId", serverId);
      const query = next.toString();
      router.replace((query ? `/plugins?${query}` : "/plugins") as Route);
    },
    [params, router],
  );

  return (
    <form onSubmit={onSubmit} className="flex flex-wrap items-center gap-2">
      <Input
        name="q"
        defaultValue={params.get("q") ?? ""}
        placeholder={t("searchPlaceholder")}
        aria-label={t("search")}
        className="w-56"
      />
      <Select
        name="category"
        defaultValue={params.get("category") ?? ""}
        aria-label={t("filterCategory")}
        className="w-44"
      >
        <option value="">{t("allCategories")}</option>
        {PLUGIN_CATEGORIES.map((value) => (
          <option key={value} value={value}>
            {t(`categories.${value}`)}
          </option>
        ))}
      </Select>
      <Select
        name="sort"
        defaultValue={params.get("sort") ?? "recommended"}
        aria-label={t("filterSort")}
        data-testid="market-sort"
        className="w-40"
      >
        {MARKET_SORTS.map((value) => (
          <option key={value} value={value}>
            {t(`sorts.${value}`)}
          </option>
        ))}
      </Select>
      <Button type="submit" variant="secondary" size="sm">
        <Search className="size-4" />
        {t("search")}
      </Button>
    </form>
  );
}