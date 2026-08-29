"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useTranslations } from "next-intl";
import {
  Activity,
  CalendarClock,
  Cloud,
  FileCode,
  Folder,
  HardDrive,
  Info,
  Layers,
  Map,
  MessageCircle,
  Puzzle,
  RefreshCw,
  SlidersHorizontal,
  CircleHelp,
  SquareTerminal,
  Zap,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import {
  SERVER_WORKSPACE_NAV_ROWS,
  isWorkspaceCategoryActive,
  workspaceHref,
  type ServerWorkspaceCategory,
} from "@/modules/servers/workspace";
import { cn } from "@/shared/lib/cn";

const CATEGORY_ICONS: Record<ServerWorkspaceCategory, LucideIcon> = {
  overview: Info,
  operations: Zap,
  frameworks: Layers,
  backups: Cloud,
  config: SlidersHorizontal,
  "host-config": HardDrive,
  monitoring: Activity,
  plugins: Puzzle,
  "plugin-configs": FileCode,
  updates: RefreshCw,
  maps: Map,
  files: Folder,
  console: SquareTerminal,
  schedule: CalendarClock,
  discord: MessageCircle,
  help: CircleHelp,
};

export function ServerWorkspaceNav({ serverId }: { serverId: number }) {
  const pathname = usePathname();
  const t = useTranslations("serverWorkspace");

  return (
    <nav
      aria-label={t("navLabel")}
      className="mb-6 overflow-hidden rounded-lg border border-line bg-surface"
    >
      {SERVER_WORKSPACE_NAV_ROWS.map((row, index) => (
        <div
          key={row.id}
          className={cn(
            "flex items-stretch",
            index > 0 && "border-t border-line",
          )}
        >
          <p className="flex w-14 shrink-0 items-center justify-center border-r border-line bg-surface-raised text-[11px] font-semibold tracking-wide text-fg-subtle sm:w-16">
            {t(`rows.${row.id}`)}
          </p>
          <ul className="flex min-w-0 flex-1 flex-wrap">
            {row.categories.map((category) => {
              const Icon = CATEGORY_ICONS[category];
              const href = workspaceHref(serverId, category);
              const active = isWorkspaceCategoryActive(
                pathname,
                serverId,
                category,
              );
              return (
                <li key={category}>
                  <Link
                    href={href}
                    aria-current={active ? "page" : undefined}
                    className={cn(
                      "inline-flex items-center gap-1.5 px-2.5 py-2 text-sm font-medium transition-colors",
                      active
                        ? "bg-primary-muted text-fg"
                        : "text-fg-muted hover:bg-surface-raised hover:text-fg",
                    )}
                  >
                    <Icon
                      className={cn(
                        "size-3.5 shrink-0",
                        active ? "text-primary" : "text-fg-subtle",
                      )}
                    />
                    {t(`categories.${category}`)}
                  </Link>
                </li>
              );
            })}
          </ul>
        </div>
      ))}
    </nav>
  );
}
