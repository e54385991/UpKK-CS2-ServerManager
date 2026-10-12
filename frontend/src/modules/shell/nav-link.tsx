"use client";

import { Link } from "@/i18n/navigation";
import type { PublicRoute as Route } from "@/i18n/public-route";
import type { LucideIcon } from "lucide-react";
import type { MouseEventHandler } from "react";
import { cn } from "@/shared/lib/cn";
import { activeNavHref, navPathMatches, type NavItem } from "@/shared/config/navigation";
import { LinkPendingHint } from "@/shared/ui/link-pending-hint";
import { useNavigationPathname } from "@/shared/ui/navigation-path";

/**
 * Sidebar / mobile-drawer navigation link. Keeps the default `<Link>` prefetch
 * so the App Shell and route payload load ahead of the click, and marks the
 * target immediately on click so feedback does not wait on `useLinkStatus`.
 */
type NavLinkProps = {
  href: Route;
  label: string;
  icon: LucideIcon;
  active?: boolean;
  navKey?: string;
  items?: readonly NavItem[];
  onClick?: MouseEventHandler<HTMLAnchorElement>;
};

export function NavLink(props: NavLinkProps) {
  const pathname = useNavigationPathname();
  const active = pathname !== null && (props.active ?? (props.items
    ? activeNavHref(pathname, props.items) === props.href : navPathMatches(pathname, props.href)));
  return <NavLinkView {...props} active={active} />;
}
function NavLinkView({ href, label, icon: Icon, active, navKey, onClick }: NavLinkProps) {
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
      <LinkPendingHint href={href} />
    </Link>
  );
}
