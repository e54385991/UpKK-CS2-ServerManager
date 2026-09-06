import Link from "next/link";
import type { Route } from "next";
import { getTranslations } from "next-intl/server";
import {
  PLUGIN_FRAMEWORK_SECTIONS,
  type MarketQuery,
  type PluginFrameworkSection,
} from "@/modules/plugins/types";
import { cn } from "@/shared/lib/cn";

export function frameworkHref(
  framework: PluginFrameworkSection,
  query: MarketQuery,
  serverId?: number,
): Route {
  const params = new URLSearchParams();
  params.set("framework", framework);
  if (query.q) params.set("q", query.q);
  if (query.category) params.set("category", query.category);
  if (serverId) params.set("serverId", String(serverId));
  return `/plugins?${params.toString()}` as Route;
}

/**
 * The marketplace's two top-level sections. Switching sections resets paging
 * because offsets do not carry across a different result set.
 */
export async function FrameworkTabs({
  active,
  query,
  serverId,
}: {
  active: PluginFrameworkSection;
  query: MarketQuery;
  serverId?: number;
}) {
  const t = await getTranslations("plugins");

  return (
    <nav
      aria-label={t("frameworkSections")}
      data-testid="market-framework-tabs"
      className="flex flex-wrap gap-1 rounded-lg border border-line bg-surface-overlay p-1"
    >
      {PLUGIN_FRAMEWORK_SECTIONS.map((framework) => (
        <Link
          key={framework}
          href={frameworkHref(framework, query, serverId)}
          aria-current={framework === active ? "page" : undefined}
          className={cn(
            "rounded-md px-3 py-1.5 text-sm font-medium transition-colors",
            framework === active
              ? "bg-surface-raised text-fg shadow-sm"
              : "text-fg-muted hover:text-fg",
          )}
        >
          {t(`frameworks.${framework}`)}
        </Link>
      ))}
    </nav>
  );
}
