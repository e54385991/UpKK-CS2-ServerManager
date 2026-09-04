"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Crosshair } from "lucide-react";
import { useTranslations } from "next-intl";
import { activeNavHref, NAV_SECTIONS } from "@/shared/config/navigation";
import { NavLink } from "@/modules/shell/nav-link";

/**
 * Persistent desktop sidebar. A client component because it renders icon
 * components and drives active-link state; it is still server-rendered for the
 * first paint and stays mounted across route changes, so navigating only
 * re-renders the page region.
 */
export function Sidebar({ isAdmin }: { isAdmin: boolean }) {
  const pathname = usePathname();
  const t = useTranslations("nav");
  const tSite = useTranslations("site");

  return (
    <aside className="hidden h-full w-64 shrink-0 flex-col overflow-y-auto border-r border-line bg-surface/60 md:flex">
      <div className="flex min-h-14 items-center gap-2.5 border-b border-line px-5 py-2">
        <Link href="/overview" className="flex min-w-0 items-center gap-2.5">
          <span className="flex size-8 shrink-0 items-center justify-center rounded-md bg-primary-muted text-primary ring-1 ring-primary/30">
            <Crosshair className="size-4.5" />
          </span>
          <span className="min-w-0 text-[12px] font-semibold leading-snug tracking-tight text-fg">
            {tSite("name")}
          </span>
        </Link>
      </div>

      <nav className="flex-1 space-y-6 overflow-y-auto px-3 py-4">
        {NAV_SECTIONS.map((section) => {
          const items = section.items.filter(
            (item) => !item.adminOnly || isAdmin,
          );
          if (items.length === 0) return null;
          const activeHref = activeNavHref(pathname, items);
          return (
            <div key={section.titleKey}>
              <p className="px-3 pb-2 text-[0.6875rem] font-semibold uppercase tracking-[0.14em] text-fg-subtle">
                {t(section.titleKey)}
              </p>
              <div className="space-y-1">
                {items.map((item) => (
                  <NavLink
                    key={item.href}
                    href={item.href}
                    label={t(item.key)}
                    icon={item.icon}
                    active={item.href === activeHref}
                  />
                ))}
              </div>
            </div>
          );
        })}
      </nav>

    </aside>
  );
}
