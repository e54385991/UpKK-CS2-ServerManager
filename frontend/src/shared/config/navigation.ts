import type { Route } from "next";
import type { LucideIcon } from "lucide-react";
import {
  LayoutDashboard,
  Server,
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
