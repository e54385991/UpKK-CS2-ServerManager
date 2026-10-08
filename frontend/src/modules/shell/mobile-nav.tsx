"use client";

import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { Link } from "@/i18n/navigation";
import { usePathname } from "@/i18n/navigation";
import { Menu, X, Crosshair } from "lucide-react";
import { useTranslations } from "next-intl";
import { NAV_SECTIONS } from "@/shared/config/navigation";
import { NavLink } from "@/modules/shell/nav-link";
import { LinkPendingHint } from "@/shared/ui/link-pending-hint";
import { Button } from "@/shared/ui/button";
import { useHydrated } from "@/shared/lib/use-hydrated";

/**
 * Hamburger + overlay drawer for viewports below `md`.
 *
 * The overlay is portaled to `document.body`. The trigger lives in the sticky
 * topbar, which uses `backdrop-blur`; that filter makes `position: fixed`
 * descendants resolve against the header instead of the viewport, so an in-tree
 * drawer overflowed and covered the page.
 *
 * Open state is tied to the current pathname so a navigation automatically
 * dismisses the drawer without an effect.
 */
export function MobileNav({ admin }: { admin?: React.ReactNode }) {
  const hydrated = useHydrated();
  const pathname = usePathname();
  const [openPath, setOpenPath] = useState<string | null>(null);
  const open = openPath === pathname;
  const t = useTranslations("nav");
  const tSite = useTranslations("site");
  const tShell = useTranslations("shell");

  useEffect(() => {
    if (!open) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpenPath(null);
    };
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    window.addEventListener("keydown", onKeyDown);
    return () => {
      document.body.style.overflow = previousOverflow;
      window.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  const overlay = open ? (
    <div className="fixed inset-0 z-50 md:hidden" role="dialog" aria-modal>
      <div
        className="absolute inset-0 bg-canvas/80 backdrop-blur-sm"
        onClick={() => setOpenPath(null)}
      />
      <div
        data-testid="console-mobile-drawer"
        className="absolute inset-y-0 left-0 flex w-72 flex-col border-r border-line bg-surface shadow-panel"
      >
        <div className="flex h-14 items-center justify-between border-b border-line px-5">
          <Link
            href="/overview"
            data-nav-key="home"
            onClick={
              pathname === "/overview" ? () => setOpenPath(null) : undefined
            }
            className="flex min-w-0 items-center gap-2.5"
          >
            <span className="flex size-8 items-center justify-center rounded-md bg-primary-muted text-primary ring-1 ring-primary/30">
              <Crosshair className="size-4.5" />
            </span>
            <span className="min-w-0 text-[12px] font-semibold leading-snug text-fg">
              {tSite("name")}
            </span>
            <LinkPendingHint href="/overview" />
          </Link>
          <Button
            variant="ghost"
            size="icon"
            aria-label={tShell("closeNav")}
            onClick={() => setOpenPath(null)}
          >
            <X className="size-5" />
          </Button>
        </div>
        <nav className="flex-1 space-y-6 overflow-y-auto px-3 py-4">
          {NAV_SECTIONS.map((section) => {
            const items = section.items.filter(
              (item) => !item.adminOnly,
            );
            if (items.length === 0) return null;
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
                      navKey={item.key}
                      items={items}
                      onClick={
                        item.href === pathname
                          ? () => setOpenPath(null)
                          : undefined
                      }
                    />
                  ))}
                </div>
                {section.items.some(item => item.adminOnly) ? <div onClick={() => setOpenPath(null)}>{admin}</div> : null}
              </div>
            );
          })}
        </nav>
      </div>
    </div>
  ) : null;

  return (
    <>
      <Button
        variant="ghost"
        size="icon"
        className="md:hidden"
        data-testid="console-mobile-open"
        aria-label={tShell("openNav")}
        aria-expanded={open}
        disabled={!hydrated}
        onClick={() => setOpenPath(pathname)}
      >
        <Menu className="size-5" />
      </Button>
      {overlay ? createPortal(overlay, document.body) : null}
    </>
  );
}
