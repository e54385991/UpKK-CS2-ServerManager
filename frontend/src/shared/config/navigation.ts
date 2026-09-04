import type { Route } from "next";
import type { LucideIcon } from "lucide-react";
import {
  LayoutDashboard,
  Server,
  ServerCog,
  Boxes,
  Bot,
  MessageCircle,
  ScrollText,
  Settings2,
} from "lucide-react";

export type NavItem = {
  readonly href: Route;
  /** Translation key under the `nav` namespace. */
  readonly key: string;
  readonly icon: LucideIcon;
  /** When true, only administrators see this entry. */
  readonly adminOnly?: boolean;
};

export type NavSection = {
  /** Translation key under the `nav` namespace. */
  readonly titleKey: string;
  readonly items: readonly NavItem[];
};

export function navPathMatches(pathname: string, href: Route): boolean {
  return pathname === href || pathname.startsWith(`${href}/`);
}

/** Return the most specific matching item so parent paths do not steal focus. */
export function activeNavHref(
  pathname: string,
  items: readonly NavItem[],
): Route | undefined {
  return items.reduce<Route | undefined>((current, item) => {
    if (!navPathMatches(pathname, item.href)) return current;
    if (!current || item.href.length > current.length) return item.href;
    return current;
  }, undefined);
}

/**
 * Primary console navigation. Declarative so the sidebar, mobile drawer, and
 * command palette render from one source of truth; labels are resolved via i18n
 * (`nav` namespace) at render time.
 */
export const NAV_SECTIONS: readonly NavSection[] = [
  {
    titleKey: "sectionOperate",
    items: [
      { href: "/overview", key: "overview", icon: LayoutDashboard },
      { href: "/servers", key: "servers", icon: Server },
      { href: "/servers/initialized" as Route, key: "initializedServers", icon: ServerCog },
      { href: "/plugins", key: "plugins", icon: Boxes },
      { href: "/assistant", key: "assistant", icon: Bot },
    ],
  },
  {
    titleKey: "sectionManage",
    items: [
      { href: "/settings/discord" as Route, key: "discord", icon: MessageCircle },
      { href: "/audit", key: "audit", icon: ScrollText, adminOnly: true },
      { href: "/settings", key: "settings", icon: Settings2, adminOnly: true },
    ],
  },
] as const;
