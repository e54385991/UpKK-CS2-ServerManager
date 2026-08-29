import type { Route } from "next";
import type { LucideIcon } from "lucide-react";
import {
  LayoutDashboard,
  Server,
  Boxes,
  Bot,
  ScrollText,
  Settings2,
} from "lucide-react";

export type NavItem = {
  readonly href: Route;
  readonly labelKey: string;
  readonly label: string;
  readonly icon: LucideIcon;
  /** When true, only administrators see this entry. */
  readonly adminOnly?: boolean;
};

export type NavSection = {
  readonly titleKey: string;
  readonly title: string;
  readonly items: readonly NavItem[];
};

/**
 * Primary console navigation. Kept declarative so the sidebar, mobile drawer,
 * and command palette all render from a single source of truth.
 */
export const NAV_SECTIONS: readonly NavSection[] = [
  {
    titleKey: "nav.section.operate",
    title: "运维",
    items: [
      {
        href: "/overview",
        labelKey: "nav.overview",
        label: "总览",
        icon: LayoutDashboard,
      },
      {
        href: "/servers",
        labelKey: "nav.servers",
        label: "服务器",
        icon: Server,
      },
      {
        href: "/plugins",
        labelKey: "nav.plugins",
        label: "插件中心",
        icon: Boxes,
      },
      {
        href: "/assistant",
        labelKey: "nav.assistant",
        label: "AI 助手",
        icon: Bot,
      },
    ],
  },
  {
    titleKey: "nav.section.manage",
    title: "管理",
    items: [
      {
        href: "/audit",
        labelKey: "nav.audit",
        label: "审计日志",
        icon: ScrollText,
        adminOnly: true,
      },
      {
        href: "/settings",
        labelKey: "nav.settings",
        label: "系统设置",
        icon: Settings2,
        adminOnly: true,
      },
    ],
  },
] as const;
