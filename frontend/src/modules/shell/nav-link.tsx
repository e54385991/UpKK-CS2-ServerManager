"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { Route } from "next";
import type { LucideIcon } from "lucide-react";
import type { MouseEventHandler } from "react";
import { cn } from "@/shared/lib/cn";
import { navPathMatches } from "@/shared/config/navigation";
import { LinkPendingHint } from "@/shared/ui/link-pending-hint";

/**
 * Sidebar / mobile-drawer navigation link. Keeps the default `<Link>` prefetch
 * so the App Shell and route payload load ahead of the click, and uses
 * `useLinkStatus` so a slow or unprefetched transition still marks the target
 * immediately.
 */
export function NavLink({
  href,
  label,
  icon: Icon,
  active: activeOverride,
  navKey,
  onClick,
}: {
  href: Route;
  label: string;
  icon: LucideIcon;
  active?: boolean;
  navKey?: string;
  onClick?: MouseEventHandler<HTMLAnchorElement>;
}) {
  const pathname = usePathname();
  const active = activeOverride ?? navPathMatches(pathname, href);

  return (
    <Link
      href={href}
      aria-current={active ? "page" : undefined}
      data-nav-key={navKey}
      onClick={onClick}
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
      <span className="min-w-0 flex-1 truncate">{label}</span>
      <LinkPendingHint />
    </Link>
  );
}
