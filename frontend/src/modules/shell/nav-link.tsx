"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { Route } from "next";
import type { LucideIcon } from "lucide-react";
import { cn } from "@/shared/lib/cn";

/**
 * Sidebar navigation link. Uses the default `<Link>` prefetch so the shared App
 * Shell and route payload are fetched ahead of the click — navigation swaps the
 * page region instantly while the shell stays mounted.
 */
export function NavLink({
  href,
  label,
  icon: Icon,
}: {
  href: Route;
  label: string;
  icon: LucideIcon;
}) {
  const pathname = usePathname();
  const active = pathname === href || pathname.startsWith(`${href}/`);

  return (
    <Link
      href={href}
      aria-current={active ? "page" : undefined}
      className={cn(
        "group relative flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors",
        active
          ? "bg-surface-overlay text-fg"
          : "text-fg-muted hover:bg-surface-overlay/60 hover:text-fg",
      )}
    >
      <span
        className={cn(
          "absolute left-0 h-5 w-0.5 rounded-full bg-primary transition-opacity",
          active ? "opacity-100" : "opacity-0",
        )}
      />
      <Icon
        className={cn(
          "size-4 shrink-0 transition-colors",
          active ? "text-primary" : "text-fg-subtle group-hover:text-fg-muted",
        )}
      />
      <span className="truncate">{label}</span>
    </Link>
  );
}
